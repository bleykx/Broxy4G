from uuid import uuid4

import pytest

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial


@pytest.mark.asyncio
async def test_dongle_repository_save_and_get_by_id(dongle_repo, session_factory):
    # Création d'un dongle
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    saved = await dongle_repo.save(dongle)
    assert saved.id == dongle.id

    # Récupération par ID
    retrieved = await dongle_repo.get_by_id(dongle.id)
    assert retrieved is not None
    assert retrieved.id == dongle.id
    assert retrieved.label == "Test Dongle"


@pytest.mark.asyncio
async def test_dongle_repository_save_and_get_by_identity_key(dongle_repo, session_factory):
    # Création d'un dongle
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    await dongle_repo.save(dongle)

    # Récupération par identity_key
    retrieved = await dongle_repo.get_by_identity_key("123456789012345")
    assert retrieved is not None
    assert retrieved.id == dongle.id
    assert retrieved.label == "Test Dongle"


@pytest.mark.asyncio
async def test_dongle_repository_save_upsert(dongle_repo, session_factory):
    # Création d'un dongle
    identity = IdentityMaterial(imei="123456789012345")
    dongle1 = Dongle(identity=identity, label="Test Dongle 1")
    saved1 = await dongle_repo.save(dongle1)

    # Mise à jour avec le même identity_key
    dongle2 = Dongle(identity=identity, label="Test Dongle 2")
    saved2 = await dongle_repo.save(dongle2)

    # Doit être le même objet
    assert saved1.id == saved2.id
    assert saved2.label == "Test Dongle 2"


@pytest.mark.asyncio
async def test_dongle_repository_list_all(dongle_repo, session_factory):
    # Création de deux dongles
    identity1 = IdentityMaterial(imei="123456789012345")
    identity2 = IdentityMaterial(imei="123456789012346")
    dongle1 = Dongle(identity=identity1, label="Test Dongle 1")
    dongle2 = Dongle(identity=identity2, label="Test Dongle 2")
    await dongle_repo.save(dongle1)
    await dongle_repo.save(dongle2)

    # Liste tous
    all_dongles = await dongle_repo.list_all()
    assert len(all_dongles) == 2
    ids = [d.id for d in all_dongles]
    assert dongle1.id in ids
    assert dongle2.id in ids


@pytest.mark.asyncio
async def test_dongle_repository_delete(dongle_repo, session_factory):
    # Création d'un dongle
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    saved = await dongle_repo.save(dongle)

    # Suppression
    deleted = await dongle_repo.delete(saved.id)
    assert deleted is True

    # Vérification de la suppression
    retrieved = await dongle_repo.get_by_id(saved.id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_dongle_repository_get_by_id_none(dongle_repo, session_factory):
    # Récupération d'un dongle inexistant
    retrieved = await dongle_repo.get_by_id(uuid4())
    assert retrieved is None


@pytest.mark.asyncio
async def test_dongle_repository_last_error_roundtrip(dongle_repo, session_factory):
    # last_error doit survivre à un aller-retour save/get_by_id
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle", last_error="X: y")
    await dongle_repo.save(dongle)

    retrieved = await dongle_repo.get_by_id(dongle.id)
    assert retrieved is not None
    assert retrieved.last_error == "X: y"


@pytest.mark.asyncio
async def test_dongle_repository_save_updates_last_error(dongle_repo, session_factory):
    # Un save upsert doit mettre à jour last_error sur l'enregistrement existant
    identity = IdentityMaterial(imei="123456789012345")
    dongle1 = Dongle(identity=identity, label="Test Dongle", last_error="première erreur")
    saved1 = await dongle_repo.save(dongle1)

    retrieved = await dongle_repo.get_by_id(saved1.id)
    assert retrieved is not None
    assert retrieved.last_error == "première erreur"

    # Mise à jour avec le même identity_key mais une nouvelle last_error
    dongle2 = Dongle(
        identity=identity, label="Test Dongle", id=saved1.id, last_error="deuxième erreur"
    )
    saved2 = await dongle_repo.save(dongle2)

    assert saved2.id == saved1.id
    updated = await dongle_repo.get_by_id(saved1.id)
    assert updated is not None
    assert updated.last_error == "deuxième erreur"