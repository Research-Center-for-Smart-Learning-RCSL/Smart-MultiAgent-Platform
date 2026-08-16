<template>
  <section>
    <h2 class="mt-8 text-sm font-semibold text-[var(--color-fg)]">
      {{ $t('admin.activities.examples.heading') }}
    </h2>
    <p class="mt-1 text-xs text-[var(--color-muted)]">
      {{ $t('admin.activities.examples.help') }}
    </p>

    <SQueryError
      v-if="query.isError.value"
      class="mt-3"
      :message="$t('admin.common.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="query.refetch()"
    />

    <p
      v-else-if="query.isPending.value"
      class="mt-3 text-xs text-[var(--color-muted)]"
      role="status"
    >
      {{ $t('admin.common.loading') }}
    </p>

    <SEmptyState
      v-else-if="examples.length === 0"
      class="mt-3"
      :icon="SparklesIcon"
      :text="$t('admin.activities.examples.empty')"
    />

    <ul
      v-else
      class="mt-3 flex flex-col gap-3"
    >
      <li
        v-for="course in examples"
        :key="course.course_key"
        class="rounded-md border border-[var(--color-border)] p-3"
        :data-testid="`example-${course.course_key}`"
      >
        <div class="flex items-start justify-between gap-4">
          <div class="flex flex-col gap-1">
            <div class="flex items-center gap-2">
              <span class="font-medium">{{ course.title }}</span>
              <SBadge
                v-if="course.fully_installed"
                size="sm"
                variant="success"
              >
                {{ $t('admin.activities.examples.installed') }}
              </SBadge>
            </div>
            <span class="font-mono text-xs text-[var(--color-muted)]">{{ course.course_key }}</span>
            <span class="text-xs text-[var(--color-muted)]">{{ course.source }}</span>
          </div>

          <SButton
            variant="primary"
            size="sm"
            :loading="installingKey === course.course_key"
            :disabled="course.fully_installed"
            :data-testid="`install-${course.course_key}`"
            @click="install(course.course_key)"
          >
            {{ $t('admin.activities.examples.install') }}
          </SButton>
        </div>

        <ul class="mt-3 flex flex-col gap-2">
          <li
            v-for="unit in course.activity_types"
            :key="unit.key"
            class="flex items-center justify-between gap-3 text-sm"
          >
            <div class="flex flex-wrap items-center gap-2">
              <span>{{ displayed(unit).name }}</span>
              <span class="font-mono text-xs text-[var(--color-muted)]">{{ unit.key }}</span>
              <SBadge
                v-if="displayed(unit).expose_payload_to_agent"
                size="sm"
                variant="warning"
              >
                {{ $t('admin.activities.examples.exposesToAgent') }}
              </SBadge>
              <SBadge
                v-if="unit.installed_type_id === null"
                size="sm"
                variant="neutral"
              >
                {{ $t('admin.activities.examples.notInstalled') }}
              </SBadge>
            </div>

            <SButton
              v-if="unit.installed_type_id !== null"
              variant="ghost"
              size="sm"
              :disabled="storedRowFor(unit) === null"
              :data-testid="`edit-${unit.key}`"
              @click="openEdit(unit.installed_type_id)"
            >
              {{ $t('admin.activities.examples.edit') }}
            </SButton>
          </li>
        </ul>
      </li>
    </ul>

    <p
      v-if="unresolvedInstalled > 0"
      class="mt-2 text-xs text-[var(--color-warning)]"
      role="status"
      data-testid="examples-stored-row-unavailable"
    >
      {{ $t('admin.activities.examples.storedRowUnavailable') }}
    </p>

    <PlatformActivityTypeDialog
      :open="editTypeId !== null"
      :row="editRow"
      @close="editTypeId = null"
      @saved="onSaved"
    />
  </section>
</template>

<script setup lang="ts">
// The platform admin's install surface for shipped examples ([R30.32]).
//
// Not shared with the activities slice's import dialog: `admin` and `activities`
// may not import each other (eslint.config.js SLICE_DEPS). They also answer
// different questions -- this one installs a course platform-wide; that one
// decides whether one project uses it.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { SparklesIcon } from '@heroicons/vue/24/outline'

import { SBadge, SButton, SEmptyState, SQueryError } from '@shared/ui'
import { useToast } from '@shared/composables'

import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { AdminActivityTypeRow, AdminCatalogueTypeRow } from '../types'
import PlatformActivityTypeDialog from './PlatformActivityTypeDialog.vue'

const { t } = useI18n()
const toast = useToast()
const qc = useQueryClient()

const installingKey = ref<string | null>(null)
const editTypeId = ref<string | null>(null)

const query = useQuery({
  queryKey: adminKeys.activityExamples(),
  queryFn: () => adminApi.listActivityExamples(),
})

const examples = computed(() => query.data.value ?? [])

// The catalogue reports each unit's *shipped* values, which stop being the truth
// once an admin edits an installed one ([R30.23] Q-4) — so both the card and the
// edit form read the stored row instead.
//
// Deliberately the unbounded platform-only listing rather than a page of the
// cross-project one the governance table uses: platform types are installed at
// setup, so they are the oldest rows and the first to age off a newest-first
// page, and resolving from there left an Edit action that opened a blank form
// and silently saved nothing. Its own query key, so the table's cache entry
// still holds the cross-project list it is built to show.
const platformTypesQuery = useQuery({
  queryKey: adminKeys.platformActivityTypes(),
  queryFn: () => adminApi.listPlatformActivityTypes(),
})

const platformTypeById = computed(() => {
  const byId = new Map<string, AdminActivityTypeRow>()
  for (const row of platformTypesQuery.data.value ?? []) byId.set(row.id, row)
  return byId
})

/** The stored row behind an installed unit, or null while it is in flight or
 *  absent. Absent is not expected — the listing is unbounded — but "offered and
 *  unusable" is the defect this component had, so it is handled rather than
 *  assumed away. */
function storedRowFor(unit: AdminCatalogueTypeRow): AdminActivityTypeRow | null {
  if (unit.installed_type_id === null) return null
  return platformTypeById.value.get(unit.installed_type_id) ?? null
}

function displayed(unit: AdminCatalogueTypeRow): {
  name: string
  expose_payload_to_agent: boolean
} {
  return storedRowFor(unit) ?? unit
}

// Say so rather than leaving a disabled button with no explanation. Held back
// while the query is in flight: an unresolved row is the normal in-flight state
// and only means something once the answer is in.
//
// `isFetching`, not `isPending`. A refetch keeps `status === 'success'`, so
// `isPending` covers only the very first load — and the two queries are
// invalidated together after an install and then race. Whenever the catalogue
// answers first, the freshly installed unit has an `installed_type_id` whose row
// is not in the still-stale platform list, and an `isPending` guard would flash
// "could not be loaded, reload the page" at an admin whose install just worked.
const unresolvedInstalled = computed(() => {
  if (platformTypesQuery.isFetching.value) return 0
  return examples.value
    .flatMap((course) => course.activity_types)
    .filter((unit) => unit.installed_type_id !== null && storedRowFor(unit) === null).length
})

const editRow = computed(() =>
  editTypeId.value === null ? null : (platformTypeById.value.get(editTypeId.value) ?? null),
)

function refreshExamplesAndTypes(): void {
  void qc.invalidateQueries({ queryKey: adminKeys.activityExamples() })
  void qc.invalidateQueries({ queryKey: adminKeys.platformActivityTypes() })
  // Installing adds rows to the governance table in the view above, which reads
  // the cross-project listing under its own key.
  void qc.invalidateQueries({ queryKey: adminKeys.activityTypes() })
}

const installMutation = useMutation({
  mutationFn: (courseKey: string) => adminApi.installActivityExample(courseKey),
  onSuccess: (report) => {
    refreshExamplesAndTypes()
    // An install is idempotent, so "created nothing" is a success rather than a
    // failure -- say which it was instead of a single generic message.
    if (report.created.length > 0) {
      toast.success(t('admin.activities.examples.installed_n', { count: report.created.length }))
    } else {
      toast.success(t('admin.activities.examples.alreadyInstalled'))
    }
  },
  onError: () => {
    refreshExamplesAndTypes()
    toast.error(t('admin.activities.examples.installFailed'))
  },
  onSettled: () => {
    installingKey.value = null
  },
})

function install(courseKey: string): void {
  installingKey.value = courseKey
  installMutation.mutate(courseKey)
}

function openEdit(typeId: string): void {
  editTypeId.value = typeId
}

function onSaved(): void {
  editTypeId.value = null
  refreshExamplesAndTypes()
}
</script>
