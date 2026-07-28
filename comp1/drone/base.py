from abc import ABC, abstractmethod

import numpy as np


class DroneAdapter(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def takeoff(self) -> None: ...

    @abstractmethod
    def land(self) -> None: ...

    @abstractmethod
    def emergency(self) -> None: ...

    @abstractmethod
    def move(self, direction: str, cm: int) -> None: ...

    @abstractmethod
    def rotate(self, direction: str, deg: int) -> None: ...

    @abstractmethod
    def flip(self, direction: str) -> None: ...

    @abstractmethod
    def get_frame(self) -> np.ndarray | None: ...

    @abstractmethod
    def battery(self) -> int: ...
