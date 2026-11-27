"""Offline pilot: merge same-source daily history without replacing observed bars.

Only accepts frames supplied by the existing verified provider pipeline.
No network, persistence, publishing, or third-party dataset access.
"""
import pandas as pd

from msk_chart_release import REQUIRED, validated_daily_history


def merge_verified_history(previous, incoming, previous_session, session,
                           expected_new_sessions):
    """Return merged history or None; caller supplies verified trading dates.

    Provider identity and adjustment basis must be checked by the caller.
    Both frames must use the same basis. Corporate actions require a full
    refresh instead of incremental merging.
    """
    try:
        old_day = pd.Timestamp(previous_session)
        new_day = pd.Timestamp(session)
        expected = pd.DatetimeIndex(pd.to_datetime(list(expected_new_sessions)))
        if (old_day >= new_day or not len(expected) or not expected.is_unique
                or not expected.is_monotonic_increasing
                or expected[0] <= old_day or expected[-1] != new_day
                or expected.tz is not None or not expected.equals(expected.normalize())):
            return None
        old = validated_daily_history(previous, previous_session)
        if old is None or incoming is None or not incoming.columns.is_unique:
            return None
        delta = incoming.loc[:, list(REQUIRED)].copy()
        idx = pd.DatetimeIndex(pd.to_datetime(delta.index, errors="raise"))
        if idx.hasnans or idx.tz is not None or not idx.equals(idx.normalize()):
            return None
        delta.index = idx
        if not idx.is_unique or not idx.is_monotonic_increasing:
            return None
        for col in REQUIRED:
            delta[col] = pd.to_numeric(delta[col], errors="raise")
        overlap = delta.loc[delta.index <= old_day]
        if old_day not in overlap.index or not overlap.index.isin(old.index).all():
            return None
        if not (overlap.to_numpy() == old.loc[overlap.index].to_numpy()).all():
            return None
        new = delta.loc[delta.index > old_day]
        if not new.index.equals(expected):
            return None
        return validated_daily_history(pd.concat([old, new]), session)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None
