from __future__ import annotations

from chrom_platform.structure_core.schemas import NormalizeStructureRequest
from chrom_platform.structure_core.service import StructureService, StructureRepositoryUnavailable, normalize_smiles


class Repository:
    def __init__(self) -> None:
        self.saved = []

    def save(self, structure):
        self.saved.append(structure)
        return structure


def test_normalization_preserves_isomeric_smiles() -> None:
    result = normalize_smiles("C[C@H](O)c1ccccc1")
    assert "@" in result.canonical_smiles
    assert result.has_stereochemistry is True
    assert result.stereochemistry_status == "specified"


def test_mixed_stereochemistry_emits_warning() -> None:
    repository = Repository()
    service = StructureService(repository)
    result = service.normalize(
        NormalizeStructureRequest(
            project_id="project-a",
            input_type="smiles",
            value="C[C@H](O)CC(F)(Cl)Br",
            preserve_stereochemistry=True,
        ),
        "str_test",
    )
    assert result.stereochemistry_status == "specified"
    assert any(item.code == "STEREOCHEMISTRY_PARTIAL" for item in result.stereochemistry_warnings)


def test_normalization_has_no_in_memory_success_fallback() -> None:
    service = StructureService(None)
    request = NormalizeStructureRequest(
        project_id="project-a", input_type="smiles", value="CCO", preserve_stereochemistry=True
    )
    try:
        service.normalize(request, "str_test")
    except StructureRepositoryUnavailable:
        pass
    else:
        raise AssertionError("missing repository must fail")
