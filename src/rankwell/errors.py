"""Errors Rankwell raises on purpose. Messages are safe to show: they never carry secrets."""


class RankwellError(Exception):
    """Base class: a problem the user can act on."""


class NotConfigured(RankwellError):
    """A setting or secret is missing."""


class StoreError(RankwellError):
    """A store endpoint (public App Store or App Store Connect) failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class PlanError(RankwellError):
    """A write plan is missing, expired, already applied, or out of date."""
