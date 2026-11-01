"""The base of Hearsay's own exceptions.

Each part of the server defines its errors next to the code that raises them; they all derive
from `HearsayError`. `hearsay.api.errors` maps the ones a request can meet to HTTP statuses.
"""


class HearsayError(Exception):
    """An error with a message fit to show the user."""
