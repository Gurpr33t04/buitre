class FetchError(RuntimeError):
    """Base class for acquisition failures."""


class FetchStatusError(FetchError):
    """
    A response was received, but it is not usable page content
    (HTTP error status, bot-challenge page, non-text payload).

    Carries the HTTP status so the crawler can record it.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        verdict: str = "error",
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)

        self.status_code = status_code
        self.verdict = verdict
        self.retry_after = retry_after
