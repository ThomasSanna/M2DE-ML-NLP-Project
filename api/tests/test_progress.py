"""Tests for the in-memory progress board of the ticket answers."""

from datetime import UTC, datetime, timedelta

from app.schemas.ticket import ProgressRead
from app.services.progress import ProgressBoard, domain_of
from app.services.websearch import WebResult

RESULTS = [
    WebResult("Eduroam macOS", "https://www.a.fr/page", None, "Supprimez le profil"),
    WebResult("CAT eduroam", "https://cat.eduroam.org", "2025-01-02", "Installeur"),
]


def test_domain_is_the_host_without_www() -> None:
    assert domain_of("https://www.tf1info.fr/a/b") == "tf1info.fr"
    assert domain_of("not a url") == ""


def test_unknown_ticket_has_no_progress() -> None:
    assert ProgressBoard().get(42) is None


def test_a_run_reports_thinking_then_its_search_then_its_sources() -> None:
    board = ProgressBoard()
    run = board.start(42)

    view = board.get(42)
    assert view is not None
    assert (view.status, view.steps, view.followup_id) == ("thinking", [], None)

    run.thinking()
    run.searching("eduroam mac")
    run.found(RESULTS)

    view = board.get(42)
    assert view is not None
    assert view.status == "searching"
    assert view.steps == [
        {"kind": "thinking"},
        {
            "kind": "search",
            "query": "eduroam mac",
            "sources": [
                {"title": "Eduroam macOS", "url": "https://www.a.fr/page", "domain": "a.fr"},
                {
                    "title": "CAT eduroam",
                    "url": "https://cat.eduroam.org",
                    "domain": "cat.eduroam.org",
                },
            ],
        },
    ]


def test_a_search_that_found_nothing_keeps_an_empty_source_list() -> None:
    board = ProgressBoard()
    run = board.start(42)

    run.searching("eduroam mac")
    run.found([])

    view = board.get(42)
    assert view is not None
    assert view.steps == [{"kind": "search", "query": "eduroam mac", "sources": []}]


def test_found_without_a_search_is_ignored() -> None:
    board = ProgressBoard()
    run = board.start(42)

    run.thinking()
    run.found(RESULTS)

    view = board.get(42)
    assert view is not None
    assert view.steps == [{"kind": "thinking"}]


def test_a_finished_run_reports_its_followup_and_ignores_late_steps() -> None:
    board = ProgressBoard()
    run = board.start(42)
    run.thinking()

    board.done(42, 7)
    run.thinking()

    view = board.get(42)
    assert view is not None
    assert (view.status, view.followup_id) == ("done", 7)
    assert view.steps == [{"kind": "thinking"}]


def test_a_failed_run_keeps_the_steps_it_reached() -> None:
    board = ProgressBoard()
    run = board.start(42)
    run.thinking()

    board.failed(42)

    view = board.get(42)
    assert view is not None
    assert (view.status, view.followup_id) == ("failed", None)
    assert view.steps == [{"kind": "thinking"}]


def test_done_and_failed_do_nothing_for_a_ticket_never_started() -> None:
    board = ProgressBoard()

    board.done(42, 7)
    board.failed(42)

    assert board.get(42) is None


def test_a_finished_run_is_not_marked_failed() -> None:
    board = ProgressBoard()
    board.start(42)
    board.done(42, 7)

    board.failed(42)

    view = board.get(42)
    assert view is not None
    assert (view.status, view.followup_id) == ("done", 7)


def test_a_running_run_is_still_marked_failed() -> None:
    board = ProgressBoard()
    run = board.start(42)
    run.searching("eduroam mac")

    board.failed(42)

    view = board.get(42)
    assert view is not None
    assert (view.status, view.followup_id) == ("failed", None)


def test_tickets_do_not_share_their_progress() -> None:
    board = ProgressBoard()
    first = board.start(42)
    board.start(43)

    first.searching("eduroam mac")

    view = board.get(43)
    assert view is not None
    assert (view.status, view.steps) == ("thinking", [])


def test_a_new_run_replaces_the_previous_one() -> None:
    board = ProgressBoard()
    board.start(42)
    board.done(42, 7)

    board.start(42)

    view = board.get(42)
    assert view is not None
    assert (view.status, view.steps, view.followup_id) == ("thinking", [], None)


def test_a_finished_run_is_purged_after_the_ttl() -> None:
    moments = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]
    board = ProgressBoard(clock=lambda: moments[0])
    board.start(42)
    board.done(42, 7)

    moments[0] += timedelta(minutes=4)
    assert board.get(42) is not None

    moments[0] += timedelta(minutes=2)
    assert board.get(42) is None


def test_a_running_run_is_kept_while_it_reports_steps() -> None:
    moments = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]
    board = ProgressBoard(clock=lambda: moments[0])
    run = board.start(42)

    for _ in range(3):
        moments[0] += timedelta(minutes=4)
        run.thinking()

    view = board.get(42)
    assert view is not None
    assert view.status == "thinking"


def test_a_running_run_without_news_is_purged_after_the_ttl() -> None:
    # Its caller died without closing it: the plugin must not spin forever.
    moments = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]
    board = ProgressBoard(clock=lambda: moments[0])
    board.start(42)

    moments[0] += timedelta(minutes=6)

    assert board.get(42) is None


def test_the_view_is_a_copy_the_caller_cannot_corrupt() -> None:
    board = ProgressBoard()
    run = board.start(42)
    run.searching("eduroam mac")
    run.found(RESULTS)

    view = board.get(42)
    assert view is not None
    view.steps[0]["sources"].clear()

    again = board.get(42)
    assert again is not None
    assert len(again.steps[0]["sources"]) == 2


def test_a_malformed_url_has_no_domain_and_does_not_break_the_run() -> None:
    assert domain_of("http://[::1") == ""

    board = ProgressBoard()
    run = board.start(42)
    run.searching("eduroam mac")
    run.found([WebResult("Broken", "http://[::1", None, "")])

    view = board.get(42)
    assert view is not None
    assert view.steps == [
        {
            "kind": "search",
            "query": "eduroam mac",
            "sources": [{"title": "Broken", "url": "http://[::1", "domain": ""}],
        }
    ]


def test_a_thinking_step_is_serialized_without_a_query_or_sources() -> None:
    dumped = ProgressRead(status="thinking", steps=[{"kind": "thinking"}]).model_dump(mode="json")

    assert dumped == {
        "status": "thinking",
        "steps": [{"kind": "thinking"}],
        "followup_id": None,
    }


def test_a_search_step_is_serialized_with_its_query_and_sources() -> None:
    dumped = ProgressRead(
        status="searching",
        steps=[
            {
                "kind": "search",
                "query": "eduroam mac",
                "sources": [
                    {"title": "Eduroam macOS", "url": "https://www.a.fr/page", "domain": "a.fr"}
                ],
            }
        ],
    ).model_dump(mode="json")

    assert dumped == {
        "status": "searching",
        "steps": [
            {
                "kind": "search",
                "query": "eduroam mac",
                "sources": [
                    {"title": "Eduroam macOS", "url": "https://www.a.fr/page", "domain": "a.fr"}
                ],
            }
        ],
        "followup_id": None,
    }
