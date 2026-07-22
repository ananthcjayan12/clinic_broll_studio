from __future__ import annotations

import argparse
import sys

from .pipeline.motion import run as run_motion
from .pipeline.plan import refine_slot
from .pipeline.orchestrator import run_stage, run_through
from .pipeline.stills import run as run_stills
from .core.state import mark_stage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--step", type=int)
    group.add_argument("--through", type=int)
    group.add_argument("--slot-still")
    group.add_argument("--slot-motion")
    group.add_argument("--slot-refine")
    parser.add_argument("--instruction", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--confirm-paid", action="store_true")
    args = parser.parse_args()
    if args.step:
        run_stage(args.run_id, args.step, force=args.force, confirm_paid=args.confirm_paid)
    elif args.through:
        run_through(args.run_id, args.through, force=args.force, confirm_paid=args.confirm_paid)
    elif args.slot_still:
        if not args.confirm_paid:
            raise RuntimeError('Explicit provider-usage confirmation is required for still generation')
        result = run_stills(args.run_id, slot_id=args.slot_still, force=True)
        # Slot-by-slot generation is the Studio's normal review workflow.  It
        # must satisfy stage 8 just like a batch run, otherwise approved stills
        # are stranded behind a stage that remains marked pending.
        mark_stage(
            args.run_id,
            8,
            "complete",
            artifacts=result.get("artifacts", []),
            summary=result.get("summary"),
        )
    elif args.slot_motion:
        if not args.confirm_paid:
            raise RuntimeError('Explicit provider-usage confirmation is required for motion generation')
        run_motion(args.run_id, slot_id=args.slot_motion, force=True)
    elif args.slot_refine:
        if not args.confirm_paid:
            raise RuntimeError('Explicit provider-usage confirmation is required for AI slot refinement')
        refine_slot(args.run_id, args.slot_refine, args.instruction)


if __name__ == "__main__":
    main()
