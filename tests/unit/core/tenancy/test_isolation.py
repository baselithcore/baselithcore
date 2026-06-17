"""Tenant-isolation guarantees: cross-tenant guards, context isolation, crypto."""

import asyncio

import pytest

from core.context import (
    get_current_tenant_id,
    reset_tenant_context,
    set_tenant_context,
)
from core.security.encryption import DecryptionError
from core.tenancy import (
    CrossTenantError,
    derive_tenant_key_material,
    require_tenant_match,
    tenant_field_encryptor,
    tenants_match,
)


class TestGuard:
    def test_match_explicit(self):
        assert tenants_match("acme", "acme")
        assert not tenants_match("acme", "other")

    def test_match_uses_context(self):
        token = set_tenant_context("acme")
        try:
            assert tenants_match("acme")
            assert not tenants_match("other")
        finally:
            reset_tenant_context(token)

    def test_require_match_raises_on_mismatch(self):
        with pytest.raises(CrossTenantError) as ei:
            require_tenant_match("acme", current="other")
        assert ei.value.resource_tenant == "acme"
        assert ei.value.current_tenant == "other"

    def test_require_match_passes_on_same(self):
        require_tenant_match("acme", current="acme")  # no raise


class TestContextIsolation:
    @pytest.mark.asyncio
    async def test_concurrent_tasks_do_not_bleed(self):
        """Each asyncio task carries its own tenant context (contextvar copy)."""
        release = asyncio.Event()
        results: dict[str, str] = {}

        async def worker(tenant: str) -> None:
            set_tenant_context(tenant)
            # Yield so both tasks are interleaved before reading back.
            await release.wait()
            results[tenant] = get_current_tenant_id()

        t1 = asyncio.create_task(worker("tenant-A"))
        t2 = asyncio.create_task(worker("tenant-B"))
        await asyncio.sleep(0)  # let both set their context and block
        release.set()
        await asyncio.gather(t1, t2)

        # No cross-task bleed: each saw exactly its own tenant.
        assert results == {"tenant-A": "tenant-A", "tenant-B": "tenant-B"}

    @pytest.mark.asyncio
    async def test_child_context_does_not_leak_to_caller(self):
        token = set_tenant_context("caller")
        try:

            async def child() -> str:
                set_tenant_context("child")
                return get_current_tenant_id()

            # Run child in its own copied context.
            got = await asyncio.create_task(child())
            assert got == "child"
            # Caller's context is unchanged by the child task.
            assert get_current_tenant_id() == "caller"
        finally:
            reset_tenant_context(token)


class TestPerTenantEncryption:
    KEYS = {"k1": "operator-base-passphrase-strong"}

    def test_same_tenant_roundtrips(self):
        enc = tenant_field_encryptor("acme", self.KEYS, "k1")
        token = enc.encrypt("super-secret")
        assert enc.decrypt(token) == "super-secret"

    def test_cross_tenant_decrypt_fails(self):
        enc_a = tenant_field_encryptor("tenant-A", self.KEYS, "k1")
        enc_b = tenant_field_encryptor("tenant-B", self.KEYS, "k1")
        token = enc_a.encrypt("A-only")
        # Authenticated encryption: B's key cannot decrypt A's ciphertext.
        with pytest.raises(DecryptionError):
            enc_b.decrypt(token)

    def test_derived_material_is_tenant_specific(self):
        a = derive_tenant_key_material(self.KEYS["k1"], "A")
        b = derive_tenant_key_material(self.KEYS["k1"], "B")
        assert a != b

    def test_key_id_preserved(self):
        enc = tenant_field_encryptor("acme", self.KEYS, "k1")
        assert enc.active_key_id == "k1"
