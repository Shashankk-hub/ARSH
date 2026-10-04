"""
audio_engine.py
Responsible for:
1. YouTube search via `yt-dlp` and continuous radio queue generation via `ytmusicapi`.
2. Audio extraction via `yt-dlp` into a temporary file, avoiding fragile live streams.
3. Audio playback via `PyQt6.QtMultimedia` (QMediaPlayer + QAudioOutput).
4. Automatic transition to the next track when the current song ends (Autoplay).

The queue, playback controls, and radio fallback are implemented here.
"""

import threading
import logging
import random
import os
import tempfile
from urllib.parse import urlsplit
from typing import List, Dict, Optional, Callable
from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
import yt_dlp
from ytmusicapi import YTMusic
from queue_logic import build_queue

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


class TrackInfo:
    """Represents a music track with its metadata."""
    def __init__(self, video_id: str, title: str, artist: str, duration: str = "", thumbnail_url: str = ""):
        self.video_id = video_id
        self.title = title
        self.artist = artist
        self.duration = duration
        self.thumbnail_url = thumbnail_url
        self.stream_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "thumbnail_url": self.thumbnail_url,
        }


class AudioEngine(QObject):
    """
    Manages music playback, queue progression, and track resolving.
    Emits PyQt signals to communicate cleanly with the UI.
    """
    # Signals for UI updates
    track_loaded = pyqtSignal(object)          # Emits TrackInfo when a track begins loading/playing
    playback_state_changed = pyqtSignal(bool)   # True = playing, False = paused
    position_changed = pyqtSignal(int)          # Current playback position in ms
    duration_changed = pyqtSignal(int)          # Total duration in ms
    queue_updated = pyqtSignal(list)            # Emits list of TrackInfo in current queue
    error_occurred = pyqtSignal(str)           # Emits error messages if streaming fails
    buffering_status = pyqtSignal(bool)         # True when buffering/resolving
    shuffle_changed = pyqtSignal(bool)          # Required by ui_widget.py's shuffle button
    repeat_changed = pyqtSignal(str)            # Required by ui_widget.py's repeat button ("off"|"all"|"one")

    # Internal signal for thread-safe player source setting
    _stream_ready = pyqtSignal(object, str, list, int)
    _radio_ready = pyqtSignal(object, list, int)

    def __init__(self):
        super().__init__()
        # Initialize YouTube Music API client (Guest mode: zero login required, no Google blocks)
        try:
            self.ytmusic = YTMusic()
        except Exception as e:
            logging.warning(f"Error initializing YTMusic: {e}. Retrying without locale...")
            self.ytmusic = YTMusic()

        # Audio playback setup
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.85)  # 85% default volume
        # Keep completed audio local while Qt plays it. Direct YouTube URLs can
        # expire or reset mid-stream, leaving FFmpeg with incomplete AAC data.
        self._audio_temp_dir = tempfile.TemporaryDirectory(prefix="arsh-audio-")

        # Queue state
        self.queue: List[TrackInfo] = []
        self.current_index: int = -1
        self.current_track: Optional[TrackInfo] = None
        self._is_changing_track = False
        self._play_request_id = 0
        self._stream_retry_count = 0
        self._handling_stream_error = False
        self._radio_fetch_id = 0
        self._radio_fetch_pending = False

        # Shuffle/repeat state (kept for ui_widget.py compatibility -- see module docstring)
        self.shuffle_enabled = False
        self.repeat_mode = "off"  # "off" | "all" | "one"

        # Connect internal thread-safe dispatcher
        self._stream_ready.connect(self._apply_stream_and_play)
        self._radio_ready.connect(self._apply_more_radio)

        # Connect QMediaPlayer native signals
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_player_error)

        # Prefer AAC/M4A for broad Qt decoder support and retry transient downloads.
        self.ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio",  # AAC/M4A plays reliably with Qt multimedia; webm/opus often goes silent
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            # Retry transient network failures while downloading the audio.
            "socket_timeout": 20,
            "retries": 3,
            "extractor_retries": 3,
        }

    # -----------------------------
    # Search & Queue Generation
    # -----------------------------

    def search_tracks(self, query: str, limit: int = 15) -> List[TrackInfo]:
        """Search YouTube using yt-dlp.

        yt-dlp is used directly because ytmusicapi can fail with
        ConnectionResetError on some networks.
        """
        query = query.strip()
        if not query:
            return []

        try:
            # Resolve pasted YouTube links directly instead of treating the URL
            # as search text. This keeps the documented link workflow usable.
            parsed_url = urlsplit(query)
            allowed_video_hosts = {
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                "music.youtube.com",
                "youtu.be",
            }
            if (
                parsed_url.scheme.lower() == "https"
                and parsed_url.hostname
                and parsed_url.hostname.lower() in allowed_video_hosts
                and parsed_url.port in (None, 443)
            ):
                with yt_dlp.YoutubeDL({**self.ydl_opts, "skip_download": True}) as ydl:
                    item = ydl.extract_info(query, download=False)
                if not item:
                    return []
                video_id = item.get("id")
                if not video_id:
                    return []
                return [TrackInfo(
                    video_id=video_id,
                    title=item.get("title") or "YouTube Track",
                    artist=item.get("uploader") or item.get("channel") or "YouTube",
                    duration=item.get("duration_string") or "",
                    thumbnail_url=item.get("thumbnail") or "",
                )]

            search_url = f"ytsearch{limit}:{query}"

            search_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": True,
                "noplaylist": True,
            }

            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(search_url, download=False)

            entries = info.get("entries", []) if info else []
            results = []

            for item in entries:
                if not item:
                    continue

                video_id = item.get("id")
                title = item.get("title") or "YouTube Track"

                if not video_id:
                    continue

                artist = (
                    item.get("uploader")
                    or item.get("channel")
                    or "YouTube"
                )

                duration = item.get("duration_string") or ""

                thumbnail = item.get("thumbnail")

                if not thumbnail:
                    thumbnail = (
                        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    )

                results.append(
                    TrackInfo(
                        video_id,
                        title,
                        artist,
                        duration,
                        thumbnail
                    )
                )

            logging.info(
                f"[yt-dlp] Found {len(results)} result(s) for '{query}'"
            )

            return results

        except Exception as e:
            logging.error("Search failed (%s)", type(e).__name__)
            return []

    def fetch_radio_queue(self, video_id: str) -> List[TrackInfo]:
        """
        Uses YouTube Music's 'Watch Playlist' algorithm to fetch the radio queue
        for the given track, ensuring continuous endless autoplay.
        """
        recommended_tracks = []
        try:
            watch_playlist = self.ytmusic.get_watch_playlist(videoId=video_id, limit=25)
            tracks_data = watch_playlist.get("tracks", [])
            for item in tracks_data:
                v_id = item.get("videoId")
                if not v_id or v_id == video_id:
                    continue  # Skip current track to prevent immediate duplicate

                title = item.get("title", "Unknown Title")
                artists = item.get("artists", [])
                artist = ", ".join(a.get("name", "") for a in artists if "name" in a) if isinstance(artists, list) else str(artists)
                length = item.get("length", "")

                thumbnails = item.get("thumbnail", [])
                thumb_url = ""
                if isinstance(thumbnails, list) and thumbnails:
                    thumb_url = thumbnails[-1].get("url", "")
                elif isinstance(thumbnails, dict):
                    thumb_url = thumbnails.get("url", "")

                track = TrackInfo(
                    video_id=v_id,
                    title=title,
                    artist=artist or "YouTube Music",
                    duration=length,
                    thumbnail_url=thumb_url
                )
                recommended_tracks.append(track)
        except Exception as e:
            logging.error(f"Error fetching radio queue for {video_id}: {e}")

        return recommended_tracks

    # -----------------------------
    # Audio Extraction
    # -----------------------------

    def download_audio_file(self, video_id: str) -> Optional[str]:
        """
        Downloads the selected audio format to a temporary file and returns its path.
        """
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            options = dict(self.ydl_opts)
            options["outtmpl"] = os.path.join(self._audio_temp_dir.name, "%(id)s.%(ext)s")
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(video_url, download=True)
                audio_path = ydl.prepare_filename(info)
                if os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
                    return audio_path
                logging.error("yt-dlp finished without producing an audio file for %s", video_id)
        except Exception as e:
            logging.error("Audio download failed (%s)", type(e).__name__)
            self.error_occurred.emit("Could not load audio for this track.")
        return None

    # -----------------------------
    # Playback Controls
    # -----------------------------

    def play_track(
        self,
        track: TrackInfo,
        populate_radio: bool = True,
        fallback_tracks: Optional[List[TrackInfo]] = None,
    ):
        """
        Initiates playback of a specific track. Runs resolution in a background thread.
        """
        self._play_request_id += 1
        request_id = self._play_request_id
        if track is not self.current_track:
            self._stream_retry_count = 0
        self._is_changing_track = True
        self.current_track = track
        # A manually selected track becomes the start of the current queue.
        # Otherwise an old queue can survive a search selection (especially
        # when radio recommendations are empty), so repeat-all returns to an
        # unrelated earlier song.
        if populate_radio:
            self._radio_fetch_id += 1
            self._radio_fetch_pending = False
            # Show available search results immediately. Don't make the queue
            # wait for the YouTube Music radio endpoint to finish.
            self.queue = build_queue(track, fallback_tracks=fallback_tracks)
            self.current_index = 0
            self.queue_updated.emit(self.queue)
        self.buffering_status.emit(True)
        self.track_loaded.emit(track)

        def _worker():
            try:
                # 1. Download a complete audio file so Qt never decodes a
                #    truncated live response after a YouTube connection reset.
                audio_path = self.download_audio_file(track.video_id)
                if not audio_path:
                    if request_id == self._play_request_id:
                        self.buffering_status.emit(False)
                        self._is_changing_track = False
                    return

                # 2. Fetch radio recommendations if requested
                radio_tracks = []
                fallback_items = fallback_tracks or []
                if populate_radio:
                    radio_tracks = self.fetch_radio_queue(track.video_id)
                    # YouTube Music's radio endpoint can be unavailable on
                    # some networks. Keep Up Next useful with search results,
                    # or find similar titles when playback started from a link.
                    if not radio_tracks and not fallback_items:
                        query = f"{track.title} {track.artist}".strip()
                        fallback_items = self.search_tracks(query, limit=10)
                    radio_tracks = build_queue(
                        track,
                        recommendations=radio_tracks,
                        fallback_tracks=fallback_items,
                    )[1:]
                if self.shuffle_enabled and radio_tracks:
                    random.shuffle(radio_tracks)

                # 3. Safely emit back to main thread
                self._stream_ready.emit(track, audio_path, radio_tracks, request_id)

            except Exception as ex:
                logging.error(f"Worker playback error: {ex}")
                if request_id == self._play_request_id:
                    self.buffering_status.emit(False)
                    self._is_changing_track = False
                    self.error_occurred.emit(str(ex))

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _apply_stream_and_play(self, track: TrackInfo, audio_path: str, radio_tracks: list, request_id: int):
        """Executes strictly on the main Qt GUI thread."""
        if request_id != self._play_request_id:
            return
        try:
            track.stream_url = audio_path
            self._handling_stream_error = False
            self.player.setSource(QUrl.fromLocalFile(audio_path))
            self.player.play()
            self.buffering_status.emit(False)
            self._is_changing_track = False

            # Always initialize the queue on the first track, even if the
            # radio fetch came back empty -- otherwise current_index stays
            # -1 forever and play_next() silently no-ops at end of track.
            if radio_tracks or self.current_index < 0 or (self.queue and self.queue[0] is track):
                self.queue = [track] + radio_tracks
                self.current_index = 0
                self.queue_updated.emit(self.queue)
        except Exception as e:
            logging.error(f"Error applying stream on main thread: {e}")
            self.buffering_status.emit(False)
            self._is_changing_track = False

    def toggle_play_pause(self):
        """Toggles between playing and paused states."""
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            if self.current_track:
                self.play_track(self.current_track, populate_radio=False)
            elif self.queue:
                self.play_index(0)

    def toggle_shuffle(self):
        """Toggles shuffle mode. When enabling, shuffles the upcoming portion of the queue."""
        self.shuffle_enabled = not self.shuffle_enabled
        if self.shuffle_enabled and self.queue:
            upcoming = self.queue[self.current_index + 1:]
            random.shuffle(upcoming)
            self.queue = self.queue[:self.current_index + 1] + upcoming
            self.queue_updated.emit(self.queue)
        self.shuffle_changed.emit(self.shuffle_enabled)

    def toggle_repeat(self):
        """Cycles repeat mode: off -> current track -> queue -> off."""
        order = ["off", "one", "all"]
        idx = order.index(self.repeat_mode)
        self.repeat_mode = order[(idx + 1) % len(order)]
        self.repeat_changed.emit(self.repeat_mode)

    def play_next(self):
        """Advances to the next track in the queue."""
        if not self.queue:
            return
        if self.current_index + 1 < len(self.queue):
            self.current_index += 1
            next_track = self.queue[self.current_index]
            self.play_track(next_track, populate_radio=False)
        else:
            if self.repeat_mode == "all":
                self.current_index = 0
                self.play_track(self.queue[self.current_index], populate_radio=False)
                return
            # Fetch more recommendations off the GUI thread so a slow network
            # cannot freeze the player when the current queue runs out.
            if self._radio_fetch_pending:
                return
            last_track = self.queue[-1]
            self._radio_fetch_id += 1
            fetch_id = self._radio_fetch_id
            self._radio_fetch_pending = True

            def _worker():
                more_tracks = self.fetch_radio_queue(last_track.video_id)
                self._radio_ready.emit(last_track, more_tracks, fetch_id)

            threading.Thread(target=_worker, daemon=True).start()

    def _apply_more_radio(self, source_track: TrackInfo, tracks: list, fetch_id: int):
        """Append recommendations returned by the background radio fetch."""
        if fetch_id != self._radio_fetch_id:
            return
        self._radio_fetch_pending = False
        # The user may have selected another track while the request was running.
        if (
            not self.queue
            or self.queue[-1] is not source_track
            or self.current_track is not source_track
            or self.current_index != len(self.queue) - 1
        ):
            return
        if self.shuffle_enabled and tracks:
            random.shuffle(tracks)
        if tracks:
            self.queue.extend(tracks)
            self.queue_updated.emit(self.queue)
            self.current_index += 1
            self.play_track(self.queue[self.current_index], populate_radio=False)

    def play_previous(self):
        """Plays the previous track or restarts the current track if > 3s in."""
        if self.player.position() > 3000:
            self.player.setPosition(0)
            return

        if self.current_index > 0:
            self.current_index -= 1
            prev_track = self.queue[self.current_index]
            self.play_track(prev_track, populate_radio=False)
        else:
            self.player.setPosition(0)

    def play_index(self, index: int):
        """Plays a track at a specific index in the queue."""
        if 0 <= index < len(self.queue):
            self.current_index = index
            self.play_track(self.queue[index], populate_radio=False)

    def seek(self, position_ms: int):
        """Seeks to a position in milliseconds."""
        self.player.setPosition(position_ms)

    def set_volume(self, volume_percent: int):
        """Sets volume from 0 to 100."""
        clamped = max(0, min(100, volume_percent))
        self.audio_output.setVolume(clamped / 100.0)

    def get_volume(self) -> int:
        return int(self.audio_output.volume() * 100)

    # -----------------------------
    # Internal Signal Handlers
    # -----------------------------

    def _on_position_changed(self, pos_ms: int):
        self.position_changed.emit(pos_ms)

    def _on_duration_changed(self, dur_ms: int):
        self.duration_changed.emit(dur_ms)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState):
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self.playback_state_changed.emit(is_playing)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus):
        # When media finishes, seamlessly trigger the next track (Continuous Autoplay)
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._is_changing_track:
            if self.repeat_mode == "one" and self.current_track:
                logging.info("Repeat-one active. Replaying current track...")
                self.play_track(self.current_track, populate_radio=False)
                return
            logging.info("Track finished naturally. Autoplaying next song...")
            self.play_next()

    def _on_player_error(self, error, error_string: str):
        """Refresh a failed, expiring YouTube URL once before giving up."""
        if error == QMediaPlayer.Error.NoError or not self.current_track:
            return
        # Qt can report a transient backend/decoder warning after playback has
        # already recovered. Retrying in that state restarts audible playback
        # and incorrectly surfaces an error while the song is playing.
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            logging.warning("Ignoring playback error while audio is still playing: %s", error_string)
            return
        self._handling_stream_error = True
        if self._is_changing_track or self._stream_retry_count >= 1:
            self.error_occurred.emit(error_string or "Audio playback failed")
            return

        self._stream_retry_count += 1
        logging.warning("Local audio playback failed; downloading the track again once: %s", error_string)
        self.error_occurred.emit("Connection interrupted; retrying playback…")
        failed_track = self.current_track
        request_id = self._play_request_id

        def retry():
            if request_id == self._play_request_id and self.current_track is failed_track:
                self.play_track(failed_track, populate_radio=False)

        QTimer.singleShot(750, retry)
