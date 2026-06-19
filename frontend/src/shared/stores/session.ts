// Re-export the session store from shared/ so that shared/ composables
// (useBanKickGuard, PermissionGate) and cross-slice consumers can import
// it without each having a direct dependency on @slices/identity.
//
// The canonical implementation stays in @slices/identity/stores/session.ts
// because it depends on the identity slice's private auth API.  This
// single re-export makes identity the *only* slice shared/ depends on,
// and all other consumers route through @shared/stores/session.
export { useSessionStore } from '@slices/identity'
