from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from rdkit import Chem

from .schemas import NormalizeStructureRequest, OriginalInput, StructureView, WarningView


class InvalidStructure(ValueError):
    pass


class StructureRepositoryUnavailable(RuntimeError):
    pass


class StructureRepository(Protocol):
    def save(self, structure: StructureView) -> StructureView: ...


@dataclass(frozen=True)
class NormalizedStructure:
    canonical_smiles: str
    has_stereochemistry: bool
    stereochemistry_status: str
    snapshot_hash: str


def normalize_smiles(value: str) -> NormalizedStructure:
    molecule = Chem.MolFromSmiles(value)
    if molecule is None:
        raise InvalidStructure("SMILES 无法解析")
    canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
    has_stereo = any(
        atom.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED for atom in molecule.GetAtoms()
    ) or any(stereo != Chem.BondStereo.STEREONONE for stereo in (bond.GetStereo() for bond in molecule.GetBonds()))
    possible = Chem.FindPotentialStereo(molecule)
    specified_centres = sum(1 for item in possible if item.specified)
    unspecified_centres = sum(1 for item in possible if not item.specified)
    if unspecified_centres:
        # The public status vocabulary has no separate "partial" state.  A
        # mixed molecule is therefore surfaced as specified plus a warning;
        # callers must not mistake it for a fully assigned stereoisomer.
        status = "unspecified" if not specified_centres else "specified"
    else:
        status = "specified" if has_stereo else "not_applicable"
    snapshot = json.dumps(
        {"schema_version": "1.0", "canonical_smiles": canonical},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return NormalizedStructure(canonical, has_stereo, status, hashlib.sha256(snapshot).hexdigest())


class StructureService:
    def __init__(self, repository: StructureRepository | None) -> None:
        self._repository = repository

    @property
    def repository_configured(self) -> bool:
        return self._repository is not None

    def normalize(
        self,
        request: NormalizeStructureRequest,
        structure_id: str,
        repository: StructureRepository | None = None,
    ) -> StructureView:
        repository = repository if repository is not None else self._repository
        if repository is None:
            raise StructureRepositoryUnavailable("结构持久化存储未配置")
        normalized = normalize_smiles(request.value)
        warnings: list[WarningView] = []
        if normalized.stereochemistry_status == "unspecified":
            warnings.append(
                WarningView(code="STEREOCHEMISTRY_UNSPECIFIED", message="结构存在未指定的潜在立体中心")
            )
        elif normalized.stereochemistry_status == "specified":
            molecule = Chem.MolFromSmiles(request.value)
            potential = Chem.FindPotentialStereo(molecule) if molecule is not None else []
            if any(not item.specified for item in potential):
                warnings.append(
                    WarningView(
                        code="STEREOCHEMISTRY_PARTIAL",
                        message="结构同时包含已指定和未指定的潜在立体中心",
                    )
                )
        structure = StructureView(
            id=structure_id,
            project_id=request.project_id,
            schema_version="1.0",
            original_input=OriginalInput(input_type="smiles", value=request.value, source_artifact_id=None),
            canonical_smiles=normalized.canonical_smiles,
            is_valid=True,
            has_stereochemistry=normalized.has_stereochemistry,
            stereochemistry_status=normalized.stereochemistry_status,  # type: ignore[arg-type]
            stereochemistry_warnings=warnings,
            snapshot_hash=normalized.snapshot_hash,
            created_at=datetime.now(timezone.utc),
        )
        return repository.save(structure)
