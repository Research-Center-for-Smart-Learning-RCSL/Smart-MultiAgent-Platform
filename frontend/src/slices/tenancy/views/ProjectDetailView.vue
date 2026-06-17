<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { useInlineRename, useToast } from '@shared/composables'
import { projectsApi, type Project } from '../api/projects'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const project = ref<Project | null>(null)
const loading = ref(false)
const error = ref(false)

async function load(): Promise<void> {
  loading.value = true
  error.value = false
  try {
    const { data } = await projectsApi.get(route.params.id as string)
    project.value = data
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

// Use the PATCH response (new name + bumped version) rather than reloading, so a
// reload failure can't mask success or strand a stale version for the next edit.
const rename = useInlineRename({
  current: () => project.value?.name ?? '',
  save: async (name) => {
    if (!project.value) return
    try {
      const { data } = await projectsApi.rename(project.value.id, name, project.value.version)
      project.value = data
    } catch (e) {
      toast.error(t('tenancy.projects.renameError'))
      throw e
    }
  },
})

async function remove(): Promise<void> {
  if (!project.value) return
  try {
    await ElMessageBox.confirm(
      t('tenancy.projects.deleteConfirm'),
      t('tenancy.projects.deleteConfirmTitle'),
      { confirmButtonText: t('tenancy.orgs.delete'), cancelButtonText: t('app.cancel'), type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await projectsApi.remove(project.value.id)
    router.push({ name: 'tenancy.projectList' })
  } catch {
    toast.error(t('identity.errors.generic'))
  }
}

onMounted(load)
</script>

<template>
  <main>
    <p v-if="loading">
      {{ $t('tenancy.projects.loading') }}
    </p>
    <p
      v-else-if="error"
      class="error"
    >
      {{ $t('tenancy.projects.loadError') }}
    </p>
    <template v-else-if="project">
      <h1 v-if="!rename.renaming.value">
        {{ project.name }}
        <button
          class="btn btn-sm"
          @click="rename.start"
        >
          {{ $t('tenancy.projects.rename') }}
        </button>
      </h1>
      <form
        v-else
        @submit.prevent="rename.save"
      >
        <label>
          {{ $t('tenancy.projects.renameLabel') }}
          <input
            v-model="rename.nameDraft.value"
            required
          >
        </label>
        <button
          type="submit"
          class="btn btn-primary"
        >
          {{ $t('app.save') }}
        </button>
        <button
          type="button"
          class="btn"
          @click="rename.cancel"
        >
          {{ $t('app.cancel') }}
        </button>
      </form>
      <p>{{ project.owner_type }} / {{ project.owner_id }}</p>
      <router-link :to="{ name: 'tenancy.projectMembers', params: { id: project.id } }">
        {{ $t('tenancy.projects.members') }}
      </router-link>
      <button
        class="btn btn-danger"
        @click="remove"
      >
        {{ $t('tenancy.orgs.delete') }}
      </button>
    </template>
  </main>
</template>
