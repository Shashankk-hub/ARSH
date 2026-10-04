import unittest
from types import SimpleNamespace

from queue_logic import build_queue


def track(video_id):
    return SimpleNamespace(video_id=video_id)


class BuildQueueTests(unittest.TestCase):
    def test_fallback_tracks_fill_queue_when_radio_is_empty(self):
        current = track("current")
        first = track("first")
        second = track("second")

        self.assertEqual(
            build_queue(current, fallback_tracks=[first, second]),
            [current, first, second],
        )

    def test_radio_recommendations_take_priority_over_fallback(self):
        current = track("current")
        recommended = track("recommended")
        fallback = track("fallback")

        self.assertEqual(
            build_queue(
                current,
                recommendations=[recommended],
                fallback_tracks=[fallback],
            ),
            [current, recommended],
        )

    def test_current_track_and_duplicate_ids_are_removed(self):
        current = track("current")
        first = track("first")
        duplicate = track("first")

        self.assertEqual(
            build_queue(
                current,
                recommendations=[current, first, duplicate],
            ),
            [current, first],
        )

    def test_unusable_recommendations_fall_back_to_search_results(self):
        current = track("current")
        fallback = track("fallback")

        self.assertEqual(
            build_queue(
                current,
                recommendations=[current],
                fallback_tracks=[fallback],
            ),
            [current, fallback],
        )

    def test_empty_recommendations_leave_only_current_track(self):
        current = track("current")

        self.assertEqual(build_queue(current), [current])


if __name__ == "__main__":
    unittest.main()
