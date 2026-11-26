"""The patient-care tools, as the builder and the call runtime know them.

A step in an agent can carry a list of tool names (``node.data["tools"]``). The patient-care set is chosen by name,
not guessed from the agent's name, so an agent called "Patient care" is not mistaken for the clinic receptionist.
"""
from typing import Any, Iterable, List, Optional

PATIENT_CARE_TOOLS = [
    ("confirm_identity", "Confirm who is on the line",
     "Checks the date of birth the person says against the record. Nothing about the medicines is shared until it matches."),
    ("get_prescription_context", "Read the prescription",
     "Gets the medicines, doses and cautions to explain. Refuses until identity is confirmed."),
    ("look_up_care_information", "Look up care information",
     "Answers a question from the company's own documents (medicine guide, care sheets, policies). Needs identity confirmed first."),
    ("record_understanding", "Note what the patient understood",
     "Records, medicine by medicine, whether the patient understood the explanation."),
    ("record_feedback", "Record feedback on the medicine",
     "Saves whether it was taken, whether it helped, satisfaction and side effects. An unhappy answer also asks for a doctor meeting and raises a care-team ticket."),
    ("request_doctor_meeting", "Request a doctor meeting",
     "Asks the clinic to arrange a meeting with the patient's doctor. It never invents a slot."),
    ("schedule_callback", "Schedule a callback",
     "Moves the follow-up call to a later time when the patient is busy."),
    ("mark_do_not_call", "Stop future calls",
     "Records that the patient does not want to be called again and cancels what is waiting."),
]
PATIENT_CARE_TOOL_NAMES = [name for name, _, _ in PATIENT_CARE_TOOLS]

# The inbound desks: what a step called Appointment Desk, Pharmacy Desk or Feedback Desk is given. (route_to and search_knowledge are not
# listed: a step gets them from its connections and its documents.)
DESK_TOOLS = [
    ("find_doctors", "Find doctors", "Matches the symptoms to a specialty and lists a senior and a standard doctor with fees and the next free times."),
    ("quote_visit_cost", "Quote the visit cost", "Works out the fee, what the insurer pays and what the patient pays."),
    ("book_doctor_appointment", "Book the appointment", "Books a doctor at a free time and gives the booking reference. A new caller becomes a patient."),
    ("check_feedback_eligibility", "Check feedback eligibility", "Feedback is open only to someone whose visit the clinic has marked completed."),
    ("record_visit_feedback", "Record visit feedback", "Saves the rating and comments about a completed visit; an unhappy answer raises a follow-up."),
    ("start_medicine_order", "Start a medicine order", "Opens an order for the medicines the caller asks for."),
    ("send_prescription_link", "Send the prescription link", "A one-time link and code to upload a prescription photo and an ID photo."),
    ("check_prescription_upload", "Read the uploaded prescription", "Lists the medicines found with price and a short explanation; unclear doses are left for the pharmacist."),
    ("confirm_order_details", "Save delivery details", "Saves the address and contact number and gives the total."),
    ("send_payment_link", "Send the payment link", "A one-time link and code to pay for the order."),
    ("check_payment", "Check the payment", "Says whether the order has been paid."),
    ("schedule_delivery", "Schedule the delivery", "Lists delivery windows and schedules the one chosen, after payment."),
]
DESK_TOOL_NAMES = [name for name, _, _ in DESK_TOOLS]


def uses_patient_care_tools(tool_names: Optional[Iterable[Any]]) -> bool:
    return any(str(t) in PATIENT_CARE_TOOL_NAMES for t in (tool_names or []))


def agent_tool_options() -> List[dict]:
    """What the builder offers in an agent step's Tools list."""
    return [{"id": i, "label": label, "help": help_} for i, label, help_ in PATIENT_CARE_TOOLS + DESK_TOOLS]
