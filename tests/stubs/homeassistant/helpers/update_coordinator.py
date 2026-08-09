from typing import Generic, TypeVar

_T = TypeVar("_T")


class UpdateFailed(Exception):
    pass

class DataUpdateCoordinator(Generic[_T]):
    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval

    async def async_request_refresh(self):
        """No-op stand-in; tests call _async_update_data directly when needed."""
        return None
