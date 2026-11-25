"""Build one FHIR R4 transaction Bundle per LabResult.

Every resource gets:
- a deterministic id (labpipe.common.ids), so a replay updates instead of duplicating;
- meta.source = "<MSH-3>#<MSH-10>", the message that produced this version of it.

Resources are built with fhir.resources' R4B models. R4B is the 2022 technical
correction of R4 and identical for the five resource types used here; building through
the models means every resource is validated against the FHIR spec as it is created.
"""

import re
from decimal import Decimal

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.specimen import Specimen

from labpipe.common import ids
from labpipe.hop1 import codes
from labpipe.hop1.model import Coded, LabObservation, LabResult

_RANGE = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)")


def lineage(result: LabResult) -> str:
    """meta.source for every resource built from this message: MSH-3#MSH-10.

    FHIR describes meta.source as "a URI that identifies the source system of the
    resource", optionally followed by "#" and an id inside that system. Here the
    system is the sending application and the id is the message control ID.
    """
    return f"{result.sending_application}#{result.msh10}"


def instant(moment) -> str:
    """An aware UTC datetime as FHIR text: 1984-10-30T08:42:48Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def coding(coded: Coded) -> dict:
    return {"system": codes.fhir_system(coded.system), "code": coded.code, "display": coded.display}


def concept(system: str, code: str, display: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}]}


def build_patient(result: LabResult) -> Patient:
    p = result.patient
    resource = {
        "id": ids.patient_id(p.assigning_authority, p.mrn),
        "meta": {"source": lineage(result)},
        "identifier": [
            {
                "use": "usual",
                "type": concept(*codes.MRN_TYPE),
                "system": codes.identifier_system("mrn", p.assigning_authority),
                "value": p.mrn,
            }
        ],
        "name": [{"family": p.family, "given": [p.given]}],
        "gender": codes.lookup(codes.GENDER, p.sex, "PID-8 sex"),
        "birthDate": p.birthdate.isoformat(),
    }
    if p.deathdate:
        resource["deceasedDateTime"] = p.deathdate.isoformat()
    return Patient(**resource)


def build_specimen(result: LabResult, patient_ref: str) -> Specimen:
    return Specimen(
        id=ids.specimen_id(result.accession),
        meta={"source": lineage(result)},
        accessionIdentifier={
            "system": codes.identifier_system("accession", result.filler),
            "value": result.accession,
        },
        type={"coding": [coding(result.specimen_type)]},
        subject={"reference": patient_ref},
        collection={"collectedDateTime": instant(result.collected_at)},
    )


def reference_range(text: str, unit: Coded) -> list[dict]:
    """OBX-7 "136-144" as a FHIR referenceRange with low and high in the result's unit.

    The original text is kept too, so nothing is lost if a range has another shape
    (">60", for example); then only the text is carried.
    """
    if not text:
        return []
    entry = {"text": text}
    match = _RANGE.fullmatch(text)
    if match:
        low, high = match.groups()
        quantity = {"unit": unit.code, "system": codes.fhir_system(unit.system), "code": unit.code}
        entry["low"] = {"value": Decimal(low), **quantity}
        entry["high"] = {"value": Decimal(high), **quantity}
    return [entry]


def build_observation(
    result: LabResult, obs: LabObservation, patient_ref: str, specimen_ref: str
) -> Observation:
    if obs.value_type != "NM":
        raise ValueError(f"OBX-{obs.set_id}: value type {obs.value_type!r} is not handled yet")
    resource = {
        "id": ids.observation_id(result.accession, obs.test.code, obs.sub_id),
        "meta": {"source": lineage(result)},
        "status": codes.lookup(codes.OBSERVATION_STATUS, obs.status, "OBX-11 status"),
        "category": [concept(*codes.OBSERVATION_CATEGORY)],
        "code": {"coding": [coding(obs.test)], "text": obs.test.display},
        "subject": {"reference": patient_ref},
        "specimen": {"reference": specimen_ref},
        "effectiveDateTime": instant(obs.observed_at),
        "issued": instant(obs.analyzed_at),
        # Decimal, not float: "14.0" stays 14.0 and 0.1 stays exactly 0.1.
        "valueQuantity": {
            "value": Decimal(obs.value),
            "unit": obs.unit.display or obs.unit.code,
            "system": codes.fhir_system(obs.unit.system),
            "code": obs.unit.code,
        },
        "referenceRange": reference_range(obs.reference_range, obs.unit),
    }
    if obs.abnormal_flag:
        code, display = codes.lookup(codes.INTERPRETATION, obs.abnormal_flag, "OBX-8 flag")
        resource["interpretation"] = [concept(codes.INTERPRETATION_SYSTEM, code, display)]
    return Observation(**resource)


def build_report(
    result: LabResult, patient_ref: str, specimen_ref: str, observation_refs: list[str]
) -> DiagnosticReport:
    return DiagnosticReport(
        id=ids.diagnostic_report_id(result.accession),
        meta={"source": lineage(result)},
        identifier=[
            {
                "system": codes.identifier_system("accession", result.filler),
                "value": result.accession,
            }
        ],
        status=codes.lookup(codes.REPORT_STATUS, result.report_status, "OBR-25 status"),
        category=[concept(*codes.REPORT_CATEGORY)],
        code={"coding": [coding(result.panel)], "text": result.panel.display},
        subject={"reference": patient_ref},
        # The visit is referenced by its identifier (PV1-19), without an Encounter resource.
        encounter={
            "identifier": {
                "system": codes.identifier_system("visit", result.patient.assigning_authority),
                "value": result.visit_number,
            }
        },
        effectiveDateTime=instant(result.collected_at),
        issued=instant(result.resulted_at),
        specimen=[{"reference": specimen_ref}],
        result=[{"reference": ref} for ref in observation_refs],
    )


def build_bundle(result: LabResult) -> Bundle:
    """One transaction Bundle for the panel: Patient, Specimen, DiagnosticReport, Observations.

    Each entry is a PUT to "<type>/<id>". A PUT with a known id is an upsert on a FHIR
    server ("create or replace this exact resource"), which is what makes resending
    the bundle safe. A transaction is all or nothing: every entry lands, or none does.
    """
    patient = build_patient(result)
    patient_ref = f"Patient/{patient.id}"
    specimen = build_specimen(result, patient_ref)
    specimen_ref = f"Specimen/{specimen.id}"
    observations = [
        build_observation(result, obs, patient_ref, specimen_ref) for obs in result.observations
    ]
    report = build_report(
        result, patient_ref, specimen_ref, [f"Observation/{o.id}" for o in observations]
    )

    entries = []
    for resource in [patient, specimen, report, *observations]:
        kind = resource.get_resource_type()
        entries.append(
            {
                "fullUrl": f"urn:uuid:{resource.id}",
                "resource": resource,
                "request": {"method": "PUT", "url": f"{kind}/{resource.id}"},
            }
        )
    return Bundle(type="transaction", entry=entries)
