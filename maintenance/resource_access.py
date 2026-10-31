"""Process-local proof of held lifecycle locks and live write window."""
from contextvars import ContextVar
operation = ContextVar('boiler_resource_operation', default=None)
write_scope = ContextVar('boiler_resource_write_scope', default=None)
