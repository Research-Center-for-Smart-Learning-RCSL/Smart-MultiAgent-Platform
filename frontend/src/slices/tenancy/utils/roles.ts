export type RoleBadgeVariant = 'info' | 'neutral'

export function roleBadgeVariant(member: { is_original_creator?: boolean }): RoleBadgeVariant {
  return member.is_original_creator ? 'info' : 'neutral'
}

export function roleLabel(
  member: { role: 'owner' | 'member'; is_original_creator?: boolean },
  t: (key: string) => string,
): string {
  if (member.is_original_creator) return t('tenancy.role.originalCreator')
  return member.role === 'owner' ? t('tenancy.role.owner') : t('tenancy.role.member')
}
