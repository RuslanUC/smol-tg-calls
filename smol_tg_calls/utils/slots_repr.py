class SlotsRepr:
    __slots__ = ()

    def __repr__(self) -> str:
        slots = set()
        for cls in self.__class__.mro():
            slots.update(getattr(cls, "__slots__", ()))

        fields = ", ".join([f"{slot}={getattr(self, slot)!r}" for slot in slots])
        return f"{self.__class__.__name__}({fields})"
