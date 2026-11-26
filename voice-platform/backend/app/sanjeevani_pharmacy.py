"""The tools of the Sanjeevani Pharmacy Desk: take a medicine order on an inbound call, from asking for it to scheduling the delivery.

The order moves in a fixed sequence and each tool refuses to run out of turn, so the agent cannot skip a step:
    start_medicine_order -> send_prescription_link -> (caller uploads prescription and ID) -> check_prescription_upload
    -> confirm_order_details -> send_payment_link -> (caller pays) -> check_payment -> schedule_delivery
The link is shown in the live transcript and the code is read out; see care_links.py. Medicines come only from the prescription the caller
uploaded; their price and one-line explanation come only from the pharmacy catalogue; anything unclear is left for the pharmacist.
"""
import logging
import random
from datetime import datetime
from typing import Any, Optional, Tuple

from . import sanjeevani_care as care
from . import care_links

logger = logging.getLogger("sanjeevani.pharmacy")


class SanjeevaniPharmacyMixin:
    def _register_sanjeevani_pharmacy_tools(self) -> None:
        reg = self.register_tool
        reg(name="start_medicine_order", handler=self._rx_start,
            description="Start an order once the caller says which medicines they want. Call it once per order.",
            parameters={"medicines": {"type": "string", "description": "What the caller asked for, in their words"}})
        reg(name="send_prescription_link", handler=self._rx_send_link,
            description="Give the caller a one-time link and code to upload their prescription (a PDF, or a photo) and a photo of their ID. Read out the code slowly.",
            parameters={})
        reg(name="check_prescription_upload", handler=self._rx_check_upload,
            description="Check whether the caller has uploaded the prescription, and get the medicines read from it with their price and a short explanation.",
            parameters={})
        reg(name="confirm_order_details", handler=self._rx_details,
            description="Save the delivery address and contact number the caller gave, and get the order total.",
            parameters={"address": {"type": "string", "description": "The full delivery address"},
                        "contact_phone": {"type": "string", "description": "A contact number for the delivery", "optional": True},
                        "contact_name": {"type": "string", "description": "The name for the delivery", "optional": True}})
        reg(name="send_payment_link", handler=self._rx_send_payment,
            description="Give the caller a one-time payment link and code for the order total. Read out the amount and the code slowly.",
            parameters={})
        reg(name="check_payment", handler=self._rx_check_payment,
            description="Check whether the caller has paid.", parameters={})
        reg(name="schedule_delivery", handler=self._rx_schedule,
            description="With no slot, list the delivery windows. With a slot (exactly as listed), schedule the delivery. Only works after payment.",
            parameters={"slot": {"type": "string", "description": "The slot value from the list, like 2026-10-26T10:00", "optional": True}})

    # ── helpers ────────────────────────────────────────────────────────────────────────────
    async def _rx_order(self, db, call_id: Any):
        """(call, tenant_id, order) for this call; order is the call's open order, or None."""
        from sqlalchemy import select
        from .models import MedicineOrder
        call, _, tenant_id = await self._desk_call(db, call_id)
        cid = self._cid(call_id)
        order = (await db.execute(select(MedicineOrder).where(
            MedicineOrder.call_id == cid, MedicineOrder.status != "cancelled").order_by(MedicineOrder.id.desc()))).scalars().first() \
            if cid is not None else None
        return call, tenant_id, order

    @staticmethod
    def _rx_item_line(i: dict) -> str:
        bits = [i.get("drug_name") or "a medicine"]
        if i.get("strength"):
            bits.append(i["strength"])
        name = " ".join(bits)
        if i.get("status") == "needs_clarification" or not i.get("dose_text"):
            how = "the dose was not clear on the prescription, so the pharmacist will confirm it before it is sent. Do not state a dose."
        else:
            parts = [i["dose_text"]]
            if i.get("timing"):
                parts.append(i["timing"])
            if i.get("duration_days"):
                parts.append(f"for {i['duration_days']} days")
            how = "as written: " + ", ".join(parts)
        about = i.get("about") or "no explanation on file; the pharmacist will explain it when it is delivered"
        price = care.money(i["price"]) if i.get("price") is not None else "price to be confirmed by the pharmacist"
        return f"{name}: {how}. About it: {about} Price: {price}."

    # ── the order ──────────────────────────────────────────────────────────────────────────
    async def _rx_start(self, medicines: str = "", call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        from .database import async_session
        from .models import MedicineOrder
        if not (medicines or "").strip():
            return "Which medicines would the caller like to order?"
        async with async_session() as db:
            call, tenant_id, order = await self._rx_order(db, call_id)
            if tenant_id is None:
                return "I'm sorry, I can't take an order on this call right now."
            if order is not None and order.status in ("awaiting_prescription", "prescription_received", "awaiting_payment"):
                order.requested_text = (medicines or "").strip()
                await db.commit()
                return f"Order {order.reference} is open. The next step is the prescription."
            patient = await self._desk_patient(db, call, call_meta(call), tenant_id, caller_number)
            order = MedicineOrder(tenant_id=tenant_id, patient_id=patient.id if patient else None, call_id=self._cid(call_id),
                                  requested_text=(medicines or "").strip(), status="awaiting_prescription",
                                  contact_phone=caller_number or getattr(call, "caller_number", None))
            db.add(order)
            await db.flush()
            order.reference = f"ORD-{order.id:05d}"
            ref = order.reference
            await db.commit()
        return (f"Order {ref} started. Next: tell the caller you need to see their prescription as proof, and call send_prescription_link. "
                "Medicines are only sent against a valid prescription.")

    async def _rx_send_link(self, call_id: Any = None, **kwargs):
        from .database import async_session
        async with async_session() as db:
            call, tenant_id, order = await self._rx_order(db, call_id)
            if order is None:
                return "There is no order yet. Start the order first."
            if order.status not in ("awaiting_prescription", "prescription_received"):
                return "The prescription for this order has already been received."
            link = await care_links.create_link(db, tenant_id=tenant_id, call_id=self._cid(call_id), order_id=order.id, kind="prescription")
            url, code, ref = care_links.link_url(link.token), link.code, order.reference
            await db.commit()
        shown = (f"Prescription upload link for order {ref}: {url}\nOne-time code: {code} (valid {care_links.LINK_TTL_MINUTES} minutes)")
        return (f"The link has been put in the call transcript. Read the code slowly: {care_links.spoken_code(code)}. Tell the caller to open the "
                "link from the transcript or message, type the code, then upload the prescription (a PDF is best, or a clear photo of the whole page) and a photo of "
                "their ID (Emirates ID or passport). Then ask them to tell you when it is done, and call check_prescription_upload."), shown

    async def _rx_check_upload(self, call_id: Any = None, **kwargs) -> str:
        from .database import async_session
        async with async_session() as db:
            _, _, order = await self._rx_order(db, call_id)
        if order is None:
            return "There is no order yet. Start the order first."
        if order.status == "awaiting_prescription" or not order.items_json:
            return "Nothing has been uploaded yet. Ask the caller to finish the upload, or offer to send a new link."
        items = order.items_json
        t = care.order_total(items)
        lines = [f"Prescription received for order {order.reference}. ID photo: {'received' if order.id_document_received else 'NOT received yet - ask for it'}.",
                 "Medicines (read each one with its short explanation, one at a time, and ask if it makes sense):"]
        lines += [f"{n}. {self._rx_item_line(i)}" for n, i in enumerate(items, 1)]
        if t["unpriced"]:
            lines.append(f"Not priced here (the pharmacist will price them): {', '.join(str(u) for u in t['unpriced'])}.")
        lines.append(f"Medicines {care.money(t['subtotal'])}; delivery {care.money(t['delivery_fee'])}; total {care.money(t['total'])}.")
        lines.append("Next: ask for the delivery address and a contact number, then call confirm_order_details. If the caller describes an emergency or "
                     "is unsure about taking a medicine, offer a doctor meeting (hand over to the Appointment Desk).")
        return "\n".join(lines)

    async def _rx_details(self, address: str = "", contact_phone: str = "", contact_name: str = "", call_id: Any = None, **kwargs) -> str:
        from .database import async_session
        if len((address or "").strip()) < 8:
            return "I need the full delivery address: building, street and area. Ask for it again."
        async with async_session() as db:
            _, _, order = await self._rx_order(db, call_id)
            if order is None or not order.items_json:
                return "The prescription has to be received before I can take the delivery details."
            order.address = address.strip()
            if contact_phone.strip():
                order.contact_phone = contact_phone.strip()
            if contact_name.strip():
                order.contact_name = contact_name.strip()
            t = care.order_total(order.items_json)
            order.amount = t["total"]
            order.status = "awaiting_payment"
            ref, total, phone = order.reference, t["total"], order.contact_phone
            await db.commit()
        return (f"Saved. Delivery to: {address.strip()}; contact {phone or 'the number they are calling from'}. The total is {care.money(total)}. "
                "Read the address back to confirm, then call send_payment_link.")

    async def _rx_send_payment(self, call_id: Any = None, **kwargs):
        from .database import async_session
        async with async_session() as db:
            call, tenant_id, order = await self._rx_order(db, call_id)
            if order is None or order.status not in ("awaiting_payment", "paid"):
                return "The delivery details have to be confirmed before payment."
            if order.payment_status == "paid":
                return "This order is already paid."
            link = await care_links.create_link(db, tenant_id=tenant_id, call_id=self._cid(call_id), order_id=order.id, kind="payment")
            url, code, ref, amount = care_links.link_url(link.token), link.code, order.reference, order.amount or 0
            await db.commit()
        shown = f"Payment link for order {ref} ({care.money(amount)}): {url}\nOne-time code: {code} (valid {care_links.LINK_TTL_MINUTES} minutes)"
        return (f"The payment link has been put in the call transcript. The amount is {care.money(amount)}. Read the code slowly: "
                f"{care_links.spoken_code(code)}. Tell the caller to open the link, type the code and confirm the payment, then tell you when it "
                "is done. Then call check_payment."), shown

    async def _rx_check_payment(self, call_id: Any = None, **kwargs) -> str:
        from .database import async_session
        async with async_session() as db:
            _, _, order = await self._rx_order(db, call_id)
        if order is None:
            return "There is no order yet."
        if order.payment_status == "paid":
            return "Payment received. Next: offer the delivery windows with schedule_delivery."
        return "Payment has not come through yet. Ask the caller to finish on the payment page, or offer a new link."

    async def _rx_schedule(self, slot: str = "", call_id: Any = None, **kwargs) -> str:
        from .database import async_session
        now = datetime.utcnow()
        async with async_session() as db:
            _, _, order = await self._rx_order(db, call_id)
            if order is None or order.payment_status != "paid":
                return "The delivery can only be scheduled after payment. Check the payment first."
            if not (slot or "").strip():
                opts = "; ".join(f"{s.label} [slot={s.key}]" for s in care.delivery_slots(now, 3))
                return f"Delivery windows: {opts}. Say them in words and ask which suits; pass the slot value back exactly."
            chosen = care.delivery_slot_from_key(now, slot)
            if chosen is None:
                opts = "; ".join(f"{s.label} [slot={s.key}]" for s in care.delivery_slots(now, 3))
                return f"That window is not one we offer. The windows are: {opts}."
            order.delivery_slot, order.delivery_start, order.status = chosen.label, chosen.start_utc, "scheduled"
            ref, addr = order.reference, order.address
            await db.commit()
        return (f"Delivery scheduled for {chosen.label}, to {addr}. The order reference is {' '.join(ref)}; read it slowly. "
                "Tell the caller the pharmacist will call before dispatch if anything on the prescription needs confirming. "
                "Then ask if they need anything else.")


def call_meta(call) -> dict:
    return (getattr(call, "metadata_json", None) or {}) if call is not None else {}
