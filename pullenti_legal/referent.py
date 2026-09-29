# -*- coding: utf-8 -*-
"""Pullenti referent emitted by the legal-identifier cartridge."""

from pullenti.ner.Referent import Referent


class LegalEntityReferent(Referent):
    OBJ_TYPENAME = "LEGALENTITY"
    ATTR_KIND = "KIND"
    ATTR_VALUE = "VALUE"
    ATTR_VALUE_START = "VALUE_START"
    ATTR_VALUE_END = "VALUE_END"

    def __init__(self, kind=None, value=None, value_start=None, value_end=None):
        super().__init__(self.OBJ_TYPENAME)
        if kind is not None:
            self.add_slot(self.ATTR_KIND, kind, False, 0)
        if value is not None:
            self.add_slot(self.ATTR_VALUE, value, False, 0)
        if value_start is not None:
            self.add_slot(self.ATTR_VALUE_START, str(value_start), False, 0)
        if value_end is not None:
            self.add_slot(self.ATTR_VALUE_END, str(value_end), False, 0)

    @property
    def kind(self):
        slot = self.find_slot(self.ATTR_KIND, None, True)
        return None if slot is None else str(slot.value)

    @property
    def value(self):
        slot = self.find_slot(self.ATTR_VALUE, None, True)
        return None if slot is None else str(slot.value)

    def to_string_ex(self, short_variant, lang=None, lev=0):
        return f"{self.kind}: {self.value}"
