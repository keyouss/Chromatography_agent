"""Project-authorized structure reads without importing scientific assembly."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Structure
from chrom_platform.modules.prediction.ports import PredictionStructurePort, StructureSnapshot
from chrom_platform.task_core.service import OperationContext


def _require_enantiomer_pair(smiles: list[str]) -> None:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise ImportError("RDKit is required for SFC enantiomer validation") from exc
    if len(smiles) != 2:
        raise ValueError("exactly two structures are required")
    molecules = [Chem.MolFromSmiles(value) for value in smiles]
    if any(molecule is None for molecule in molecules):
        raise ValueError("invalid structure")
    molecules = [molecule for molecule in molecules if molecule is not None]
    if Chem.MolToSmiles(molecules[0], isomericSmiles=False) != Chem.MolToSmiles(molecules[1], isomericSmiles=False):
        raise ValueError("structures have different connectivity")
    labels = []
    for molecule in molecules:
        Chem.AssignStereochemistry(molecule, cleanIt=True, force=True)
        ranks = Chem.CanonicalRankAtoms(molecule, breakTies=False)
        centers = Chem.FindMolChiralCenters(molecule, includeUnassigned=True, includeCIP=True)
        by_rank = {ranks[index]: label for index, label in centers}
        if not by_rank or any(label not in {"R", "S"} for label in by_rank.values()):
            raise ValueError("stereochemistry is unspecified")
        labels.append(by_rank)
    if labels[0].keys() != labels[1].keys() or any(labels[0][key] == labels[1][key] for key in labels[0]):
        raise ValueError("structures are not an enantiomeric inversion")


class SQLAlchemyPredictionStructurePort(PredictionStructurePort):
    def __init__(self, session: Session) -> None:
        self.session = session

    def load_many(self, ctx: OperationContext, project_id: str, structure_refs: list[str]) -> list[StructureSnapshot]:
        rows = self.session.scalars(
            select(Structure).where(
                Structure.project_id == project_id,
                Structure.id.in_(structure_refs),
                Structure.is_valid.is_(True),
            )
        ).all()
        by_id = {row.id: row for row in rows}
        if set(by_id) != set(structure_refs):
            missing = sorted(set(structure_refs) - set(by_id))
            from chrom_platform.core.errors import APIError
            raise APIError(404, "RESOURCE_NOT_FOUND", "结构不存在或不属于当前项目", details={"structure_refs": missing})
        return [StructureSnapshot(id=ref, project_id=project_id, normalized_smiles=by_id[ref].canonical_smiles) for ref in structure_refs]

    def validate_enantiomer_pair(self, ctx: OperationContext, project_id: str, structures: list[StructureSnapshot], *, chromatography_type: str) -> None:
        if len(structures) != 2 or structures[0].id == structures[1].id:
            from chrom_platform.core.errors import APIError
            raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC 需要两个不同的结构")
        rows = self.session.scalars(select(Structure).where(Structure.id.in_([item.id for item in structures]))).all()
        if len(rows) != 2 or any(row.project_id != project_id or not row.has_stereochemistry for row in rows):
            from chrom_platform.core.errors import APIError
            raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC 需要项目内已指定立体化学的结构")
        try:
            _require_enantiomer_pair([row.canonical_smiles for row in rows])
        except (ImportError, ValueError):
            from chrom_platform.core.errors import APIError
            raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC 两个结构必须是互为对映体") from None

    def validate_chiral_proposal(self, ctx: OperationContext, project_id: str, structure: StructureSnapshot, *, proposal_id: str) -> None:
        return None
