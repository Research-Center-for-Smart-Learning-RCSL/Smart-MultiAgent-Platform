import { computed, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { projectsApi, tenancyKeys } from '@slices/tenancy'

interface BreadcrumbItem {
  label: string
  to?: { name: string; params?: Record<string, string> }
}

/**
 * Shared composable that queries the project name and builds a breadcrumb
 * array of [Projects link, Project name link, ...extra items].
 *
 * Eliminates the duplicated projectQuery + breadcrumbs computed that was
 * copy-pasted across AgentListView, RagConfigListView, GraphragConfigListView,
 * and McpEgressAllowlistView.
 */
export function useProjectBreadcrumbs(
  projectId: string | Ref<string>,
  extraItems: BreadcrumbItem[] | Ref<BreadcrumbItem[]> = [],
) {
  const { t } = useI18n()
  const pid = typeof projectId === 'string' ? projectId : projectId.value

  const projectQuery = useQuery({
    queryKey: tenancyKeys.project(pid),
    queryFn: async () => (await projectsApi.get(pid)).data,
  })

  const projectName = computed(
    () => projectQuery.data.value?.name ?? pid.slice(0, 8),
  )

  const breadcrumbs = computed<BreadcrumbItem[]>(() => {
    const extra = Array.isArray(extraItems)
      ? extraItems
      : extraItems.value
    return [
      {
        label: t('agents.breadcrumb.projects'),
        to: { name: 'tenancy.projectList' },
      },
      {
        label: projectName.value,
        to: { name: 'tenancy.projectDetail', params: { id: pid } },
      },
      ...extra,
    ]
  })

  return { projectQuery, projectName, breadcrumbs }
}
