"""Shared local identity boundary for the two Via dashboards."""
from .store import IdentityStore, IdentityError
from .middleware import IdentityGate, workspace_host

__all__ = ['IdentityStore', 'IdentityError', 'IdentityGate', 'workspace_host']
