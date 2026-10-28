"""Approve-dialog wiring contract (app/page.tsx).

The approval flow is the one product-critical client interaction, and it is
invisible to every data-level golden case: the dialog is pure client state.
The review-fixes reconciliation rebuilt the board card and dropped the
``<ApproveDialog>`` render while keeping the ``Approve`` buttons - the click
set state that nothing consumed, and approval went silently dead on the
deployed board (found live, 2026-10-26). These source-contract checks pin the
wiring so a future card rebuild cannot strand the buttons again.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "app" / "page.tsx").read_text(encoding="utf-8")


def test_the_dialog_mount_survives_rewrites():
    assert "<ApproveDialog" in PAGE, (
        "app/page.tsx no longer renders <ApproveDialog>. The Approve buttons "
        "call setTarget(row), but without this mount the click sets state "
        "nothing consumes - a dead button on the live board."
    )
    assert "onClose={closeDialog}" in PAGE and "onApproved={load}" in PAGE, (
        "The ApproveDialog mount lost its close/refresh wiring."
    )


def test_every_approve_affordance_feeds_the_dialog():
    # The card button routes through the onApprove prop and the shared-decision
    # chip calls setTarget directly - both must feed the dialog, and no second,
    # orphaned approval path may exist.
    assert PAGE.count("onApprove={setTarget}") == 1, (
        "The card Approve button must hand setTarget down as its onApprove "
        "prop so the click routes into the dialog."
    )
    assert PAGE.count("onClick={() => setTarget(row)}") >= 1, (
        "The chip-approve must call setTarget(row) directly."
    )
    assert "className=\"approve-dialog\"" in PAGE, (
        "The dialog element itself (className approve-dialog) is missing from "
        "page.tsx."
    )


def test_the_bulk_review_affordance_feeds_the_dialog():
    # The no-data group's bulk pass sets the queue and opens the same dialog
    # on its first pending store - one token, one sequential pass, each write
    # re-gated server-side. It must not open a second approval path.
    assert "setQueue(pendingRows)" in PAGE and "setTarget(pendingRows[0])" in PAGE, (
        "The bulk-review button must route through setQueue + setTarget into "
        "the one shared ApproveDialog."
    )
    assert "queue={queue ?? undefined}" in PAGE, (
        "The ApproveDialog mount must receive the bulk queue."
    )
    assert "closeDialog = useCallback" in PAGE and "setQueue(null)" in PAGE, (
        "Closing the sheet must drop both the single target and the queue, "
        "or a stale bulk pass persists past its dialog."
    )
