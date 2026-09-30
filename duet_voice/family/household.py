"""SmartThings Family Care household: presence and inactivity.

The public SmartThings REST API does not expose Family Care inactivity alerts.
The mock household is the demo backend. There is no real Family Care adapter
in this repository; do not invent members or inactivity from device lists.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


class HouseholdError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DEMO_HOME: Dict[str, Any] = {
    "home_id": "home-family",
    "name": "Family home",
    "rooms": ["Bedroom", "Living room", "Kitchen"],
    "source": "smartthings_family_care",
}

DEMO_MEMBERS: Dict[str, Dict[str, Any]] = {
    "member-mum": {
        "member_id": "member-mum",
        "name": "Mum",
        "role": "care_recipient",
        "room": "Bedroom",
        "presence": "home",
        "last_motion_minutes": 240,
        "inactivity_alert": True,
        "watch_paired": True,
        "watch_on_wrist": False,
        "watch_id": "watch-mum",
        "online": True,
        "source": "smartthings_family_care",
    },
    "member-dad": {
        "member_id": "member-dad",
        "name": "Dad",
        "role": "care_recipient",
        "room": "Living room",
        "presence": "home",
        "last_motion_minutes": 8,
        "inactivity_alert": False,
        "watch_paired": True,
        "watch_on_wrist": True,
        "watch_id": "watch-dad",
        "online": True,
        "source": "smartthings_family_care",
    },
}

DEMO_CONTACTS: Dict[str, Dict[str, Any]] = {
    "contact-me": {
        "contact_id": "contact-me",
        "name": "Me",
        "kind": "user",
        "phone": "5550100",
        "source": "mock_contacts",
    },
    "contact-priya": {
        "contact_id": "contact-priya",
        "name": "Priya",
        "kind": "caregiver",
        "phone": "5550199",
        "source": "mock_contacts",
    },
}


class FamilyHousehold:
    def list_members(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_member(self, member_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def list_contacts(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def home(self) -> Dict[str, Any]:
        return {}


class MockFamilyHousehold(FamilyHousehold):
    def __init__(
        self,
        members: Optional[Dict[str, Dict[str, Any]]] = None,
        contacts: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.members = copy.deepcopy(members or DEMO_MEMBERS)
        self.contacts = copy.deepcopy(contacts or DEMO_CONTACTS)
        self.home_meta = copy.deepcopy(DEMO_HOME)
        self.fail_next: Optional[str] = None
        self.simulated = True

    def home(self) -> Dict[str, Any]:
        anyone = any(m.get("presence") == "home" for m in self.members.values())
        return dict(self.home_meta, anyone_home=anyone, simulated=True)

    def _maybe_fail(self) -> None:
        mode = self.fail_next
        self.fail_next = None
        if mode == "auth":
            raise HouseholdError("auth", "SmartThings Family Care authentication failed")
        if mode == "timeout":
            raise HouseholdError("timeout", "SmartThings Family Care timed out")

    def list_members(self) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [dict(v, simulated=True) for v in self.members.values()]

    def get_member(self, member_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        rec = self.members.get(member_id)
        if rec is None:
            raise HouseholdError("not_found", "No household member '%s'" % member_id)
        payload = dict(rec)
        payload["status"] = "ok"
        payload["simulated"] = True
        if payload.get("watch_on_wrist") is False:
            payload["instruction"] = (
                "This member is home but the Galaxy Watch is off-wrist. "
                "Do not invent a heart rate. Offer inactivity context or a caregiver text."
            )
        elif payload.get("inactivity_alert"):
            payload["instruction"] = (
                "SmartThings Family Care reports unusual inactivity. "
                "Do not invent a medical diagnosis. Offer to check the Watch after Knox consent, "
                "or to text a caregiver."
            )
        else:
            payload["instruction"] = (
                "Presence is recent. Do not invent an emergency. "
                "Watch vitals still need Knox consent."
            )
        return payload

    def list_contacts(self) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [dict(v, simulated=True) for v in self.contacts.values()]

    def apply_event(self, member_id: str, **fields: Any) -> None:
        if member_id in self.members:
            self.members[member_id].update(fields)
