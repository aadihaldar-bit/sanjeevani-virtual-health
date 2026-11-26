# Security and privacy

Health calls are sensitive, so the rules live in code the model cannot talk around.

## Guardrails

| Rule | Enforcement |
|---|---|
| Identity first | No medicine detail until the date of birth matches. Two wrong answers end the call with nothing shared. |
| No invented facts | A doctor, time, fee or booking reference is only said if a tool returned it. |
| No medical advice | Questions about other medicines or doses go to the doctor. Replies that tell someone to stop or change a medicine are removed. |
| Unclear prescriptions | A medicine that cannot be read clearly is not read out; a pharmacist confirms it. |
| Emergency exit | Emergency words stop the flow and send the person to emergency care. |
| Consent and limits | Calls need consent, respect do-not-call, calling hours and daily caps. Alert calls wait for a person to press Call now. |

## Data handling

- **Audit log:** keeps an allow-list of fields and masks phone numbers.
- **KYC:** keeps the name, birth year and last four Aadhaar digits only; face data is wiped when a session ends; personal fields are stripped after 90 days.
- **Vitals:** the video is deleted after analysis; readings are never written to the database.
- **Tenant isolation:** every query, document, vector store and storage bucket is scoped to one hospital, and tests check that one tenant cannot see another's data.

## Secrets

- No real keys are committed. Each app has a `.env.example` listing every setting with empty or placeholder values; copy it to `.env` and fill in your own.
- Organisation-level provider keys are stored encrypted and never returned by the API.
- A gitleaks configuration (`voice-platform/.gitleaks.toml`) and a security-audit workflow run in CI.
