// openapi-typescript-codegen types multipart binary fields as `string` (the OpenAPI
// `format: binary` maps to `string` in the generated `formData` models), but the
// request core appends the real File/Blob to FormData unchanged — the string type is a
// codegen artifact, not a runtime contract. This bridges that quirk at one documented
// site instead of repeating `file as unknown as string` at every upload call.
export function asBinaryFormField(file: File): string {
  return file as unknown as string
}
