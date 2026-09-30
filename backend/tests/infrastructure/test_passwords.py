"""Hash Argon2id, rehash y verificación con hash ficticio."""

from app.infrastructure.passwords import Argon2Policy, PasswordHasher


def test_hash_and_verify_roundtrip() -> None:
    hasher = PasswordHasher()
    digest = hasher.hash("correct-horse-battery")

    assert digest != "correct-horse-battery"
    assert hasher.verify(digest, "correct-horse-battery")
    assert not hasher.verify(digest, "wrong-password")


def test_needs_rehash_when_policy_changes() -> None:
    weak = PasswordHasher(
        Argon2Policy(time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=8)
    )
    strong = PasswordHasher(
        Argon2Policy(time_cost=2, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16)
    )
    digest = weak.hash("rehash-me")

    assert strong.needs_rehash(digest)
    assert strong.verify(digest, "rehash-me")
    updated = strong.hash("rehash-me")
    assert not strong.needs_rehash(updated)


def test_verify_or_dummy_hides_missing_user() -> None:
    hasher = PasswordHasher()

    assert not hasher.verify_or_dummy(None, "any-password")
    assert hasher.verify_or_dummy(hasher.hash("real"), "real")
