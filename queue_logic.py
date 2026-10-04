"""Small, dependency-free helpers for building the playback queue."""


def build_queue(current_track, recommendations=None, fallback_tracks=None):
    """Return the current track plus unique recommendations or fallback tracks.

    Recommendations take priority. If they contain no usable tracks after
    removing the current item and duplicates, search results are used instead.
    """
    current_id = current_track.video_id

    def unique_upcoming(tracks):
        seen = {current_id}
        upcoming = []
        for track in tracks or []:
            video_id = getattr(track, "video_id", None)
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            upcoming.append(track)
        return upcoming

    upcoming = unique_upcoming(recommendations)
    if not upcoming:
        upcoming = unique_upcoming(fallback_tracks)
    return [current_track, *upcoming]
