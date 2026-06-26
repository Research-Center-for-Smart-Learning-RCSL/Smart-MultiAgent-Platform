<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Squares2X2Icon,
  ShareIcon,
  ChatBubbleLeftRightIcon,
  ChatBubbleLeftIcon,
  ArrowRightIcon,
  ServerStackIcon,
  KeyIcon,
  LockClosedIcon,
  ArrowsRightLeftIcon,
} from '@heroicons/vue/24/outline'
import { SButton, ThemeToggle } from '@shared/ui'
import { useDocumentMeta, useRevealOnScroll } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { useRecentChatrooms, type Chatroom } from '@slices/conversation'
import AgentConstellation from '@app/components/AgentConstellation.vue'
import BrandLogo from '@app/components/BrandLogo.vue'
import LandingIntro from '@app/components/LandingIntro.vue'

const { t } = useI18n()

// Full-screen brand intro on every entry. Read the motion preference
// synchronously so the overlay is present from the first paint (no hero flash)
// and is skipped outright for reduced-motion users.
const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches
const introActive = ref(!prefersReducedMotion)

// The hero constellation stays hidden under the intro so the overlay can dock
// its assembled glyph onto this exact slot and swap in seamlessly; reduced-motion
// users (no intro) see it from the start.
const heroVisualEl = ref<HTMLElement | null>(null)
const heroRevealed = ref(!introActive.value)
const session = useSessionStore()
const workspace = useWorkspaceStore()

// `Me` carries no display name, so the greeting falls back to the email
// local-part — friendlier than the full address on a hero.
const displayName = computed(() => session.me?.email.split('@')[0] ?? '')

const features = [
  { key: 'compose', icon: Squares2X2Icon },
  { key: 'orchestrate', icon: ShareIcon },
  { key: 'chat', icon: ChatBubbleLeftRightIcon },
] as const

const trust = [
  { key: 'selfHosted', icon: ServerStackIcon },
  { key: 'byoKey', icon: KeyIcon },
  { key: 'encrypted', icon: LockClosedIcon },
  { key: 'noLockIn', icon: ArrowsRightLeftIcon },
] as const

const { el: featuresEl, revealed } = useRevealOnScroll()

// Recent chatrooms for the authenticated "jump back in" rail, via the shared
// composable (one cache entry + one network fan-out shared with the sidebar).
const { rooms: recentRooms, isLoading: recentLoading } = useRecentChatrooms(
  () => workspace.projectId,
  { limit: 4, enabled: () => session.isAuthenticated },
)
const lastRoom = computed<Chatroom | null>(() => recentRooms.value[0] ?? null)

// Show the onboarding copy only once the query has settled with no rooms —
// never while it is still loading, so a returning user is not flashed a
// misleading "nothing here yet" hero on every visit.
const recentSettledEmpty = computed(() => !recentLoading.value && !lastRoom.value)

useDocumentMeta({
  title: () => t('app.landing.metaTitle'),
  description: () => t('app.landing.metaDescription'),
})
</script>

<template>
  <div class="landing">
    <LandingIntro
      v-if="introActive"
      :target="heroVisualEl"
      @reveal="heroRevealed = true"
      @done="introActive = false"
    />
    <header class="landing__nav">
      <BrandLogo />
      <div class="landing__nav-actions">
        <ThemeToggle />
        <template v-if="session.isAuthenticated">
          <SButton
            variant="primary"
            as="router-link"
            to="/orgs"
          >
            {{ t('app.landing.enterWorkspace') }}
          </SButton>
        </template>
        <template v-else>
          <SButton
            class="landing__nav-login"
            variant="ghost"
            as="router-link"
            to="/login"
          >
            {{ t('app.landing.logIn') }}
          </SButton>
          <SButton
            variant="primary"
            as="router-link"
            to="/register"
          >
            {{ t('app.landing.getStarted') }}
          </SButton>
        </template>
      </div>
    </header>

    <main class="landing__main">
      <section class="hero">
        <div
          class="hero__bg"
          aria-hidden="true"
        />
        <div class="hero__copy">
          <template v-if="session.isAuthenticated">
            <h1 class="hero__title">
              {{ t('app.landing.welcomeBack', { name: displayName }) }}
            </h1>
            <p class="hero__subtitle">
              {{ recentSettledEmpty ? t('app.landing.authedSubtitleEmpty') : t('app.landing.authedSubtitle') }}
            </p>
            <div class="hero__actions">
              <SButton
                v-if="lastRoom"
                variant="primary"
                size="lg"
                as="router-link"
                :to="`/chatrooms/${lastRoom.id}`"
              >
                {{ t('app.landing.openRecentRoom') }}
              </SButton>
              <SButton
                :variant="lastRoom ? 'secondary' : 'primary'"
                size="lg"
                as="router-link"
                to="/orgs"
              >
                {{ t('app.landing.enterWorkspace') }}
              </SButton>
            </div>
          </template>
          <template v-else>
            <p class="hero__eyebrow">
              {{ t('app.landing.eyebrow') }}
            </p>
            <h1 class="hero__title">
              <i18n-t
                keypath="app.landing.headline"
                tag="span"
                scope="global"
              >
                <template #highlight>
                  <span class="hero__title-accent">{{ t('app.landing.headlineHighlight') }}</span>
                </template>
              </i18n-t>
            </h1>
            <p class="hero__subtitle">
              {{ t('app.landing.subtitle') }}
            </p>
            <div class="hero__actions">
              <SButton
                variant="primary"
                size="lg"
                as="router-link"
                to="/register"
              >
                {{ t('app.landing.getStarted') }}
              </SButton>
              <SButton
                variant="secondary"
                size="lg"
                as="router-link"
                to="/login"
              >
                {{ t('app.landing.logIn') }}
              </SButton>
            </div>
            <ul class="trust">
              <li
                v-for="item in trust"
                :key="item.key"
                class="trust__item"
              >
                <component
                  :is="item.icon"
                  class="trust__icon"
                  aria-hidden="true"
                />
                {{ t(`app.landing.trust.${item.key}`) }}
              </li>
            </ul>
          </template>
        </div>
        <div
          ref="heroVisualEl"
          class="hero__visual"
          :class="{ 'hero__visual--pending': !heroRevealed }"
        >
          <AgentConstellation />
        </div>
      </section>

      <section
        v-if="session.isAuthenticated && recentRooms.length"
        class="recent"
        :aria-label="t('app.landing.recentTitle')"
      >
        <h2 class="recent__title">
          {{ t('app.landing.recentTitle') }}
        </h2>
        <div class="recent__grid">
          <RouterLink
            v-for="room in recentRooms"
            :key="room.id"
            class="recent__card"
            :to="`/chatrooms/${room.id}`"
          >
            <span class="recent__icon">
              <ChatBubbleLeftIcon aria-hidden="true" />
            </span>
            <span class="recent__name">{{ room.name }}</span>
            <ArrowRightIcon
              class="recent__arrow"
              aria-hidden="true"
            />
          </RouterLink>
        </div>
      </section>

      <section
        v-else-if="!session.isAuthenticated"
        ref="featuresEl"
        class="features"
        :class="{ 'features--in': revealed }"
        :aria-label="t('app.landing.featuresLabel')"
      >
        <article
          v-for="(feature, i) in features"
          :key="feature.key"
          class="feature"
          :style="{ transitionDelay: `${i * 90}ms` }"
        >
          <span class="feature__icon">
            <component
              :is="feature.icon"
              aria-hidden="true"
            />
          </span>
          <h2 class="feature__title">
            {{ t(`app.landing.features.${feature.key}.title`) }}
          </h2>
          <p class="feature__desc">
            {{ t(`app.landing.features.${feature.key}.desc`) }}
          </p>
        </article>
      </section>
    </main>

    <footer class="landing__footer">
      <BrandLogo size="sm" />
      <span class="landing__footer-tagline">{{ t('app.landing.footerTagline') }}</span>
    </footer>
  </div>
</template>

<style scoped>
.landing {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 24px;
}

/* -- Nav -- */
.landing__nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 72px;
  flex-shrink: 0;
}

.landing__nav-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* -- Main / hero -- */
.landing__main {
  flex: 1;
}

.hero {
  position: relative;
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  align-items: center;
  gap: 48px;
  padding: 48px 0 64px;
}

/* Decorative depth: a soft radial wash plus a faint dot grid, both fading out
   before the edges so text never sits on busy texture. */
.hero__bg {
  position: absolute;
  inset: -40px -24px 0;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(
      60% 70% at 70% 30%,
      color-mix(in srgb, var(--color-accent) 12%, transparent),
      transparent 70%
    ),
    radial-gradient(var(--color-border) 1px, transparent 1px);
  background-size: auto, 22px 22px;
  -webkit-mask-image: radial-gradient(80% 80% at 50% 40%, #000 40%, transparent 100%);
  mask-image: radial-gradient(80% 80% at 50% 40%, #000 40%, transparent 100%);
  opacity: 0.5;
}

.hero__copy,
.hero__visual {
  position: relative;
  z-index: 1;
}

.hero__copy {
  animation: hero-rise 0.6s ease-out both;
}

.hero__eyebrow {
  display: inline-block;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-info-on);
  background: var(--color-info-tint);
  padding: 4px 12px;
  border-radius: var(--radius-full);
  margin: 0 0 20px;
}

.hero__title {
  font-size: clamp(2rem, 4vw, 3rem);
  font-weight: 700;
  line-height: 1.1;
  color: var(--color-fg);
  margin: 0 0 16px;
}

/* High-contrast accent gradient on the keyword. Tokens shift per theme so the
   stops stay readable in light and dark; forced-colors mode falls back to a
   solid system color so the word never disappears. */
.hero__title-accent {
  background: linear-gradient(90deg, var(--color-accent), var(--color-accent-2));
  -webkit-background-clip: text;
  background-clip: text;
  color: var(--color-accent);
  -webkit-text-fill-color: transparent;
}

@media (forced-colors: active) {
  .hero__title-accent {
    color: currentColor;
    -webkit-text-fill-color: currentColor;
  }
}

.hero__subtitle {
  font-size: 1.125rem;
  line-height: 1.6;
  color: var(--color-muted);
  max-width: 30rem;
  margin: 0 0 32px;
}

.hero__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.hero__visual {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Hidden under the intro overlay so its docked glyph can swap in seamlessly;
   no transition, so the swap is a single frame with no double image. */
.hero__visual--pending {
  opacity: 0;
}

/* -- Trust strip -- */
.trust {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
  list-style: none;
  margin: 28px 0 0;
  padding: 0;
}

.trust__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8125rem;
  color: var(--color-muted);
}

.trust__icon {
  width: 16px;
  height: 16px;
  color: var(--color-accent);
  flex-shrink: 0;
}

/* -- Features -- */
.features {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
  padding: 32px 0 72px;
}

.feature {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  padding: 24px;
  opacity: 0;
  transform: translateY(16px);
  transition:
    opacity var(--transition-slow),
    transform var(--transition-normal),
    border-color var(--transition-normal),
    box-shadow var(--transition-normal);
}

.features--in .feature {
  opacity: 1;
  transform: translateY(0);
}

.feature:hover {
  border-color: var(--color-accent);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.feature__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: var(--radius-lg);
  background: var(--color-info-tint);
  color: var(--color-info-on);
  margin-bottom: 16px;
}

.feature__icon :deep(svg) {
  width: 24px;
  height: 24px;
}

.feature__title {
  font-size: 1.0625rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 8px;
}

.feature__desc {
  font-size: 0.9375rem;
  line-height: 1.55;
  color: var(--color-muted);
  margin: 0;
}

/* -- Recent chatrooms (authenticated) -- */
.recent {
  padding: 8px 0 72px;
}

.recent__title {
  font-size: 1.0625rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 16px;
}

.recent__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.recent__card {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  padding: 16px 18px;
  text-decoration: none;
  color: var(--color-fg);
  transition:
    border-color var(--transition-normal),
    box-shadow var(--transition-normal),
    transform var(--transition-normal);
}

.recent__card:hover {
  border-color: var(--color-accent);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.recent__card:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.recent__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: var(--radius-lg);
  background: var(--color-info-tint);
  color: var(--color-info-on);
}

.recent__icon :deep(svg) {
  width: 20px;
  height: 20px;
}

.recent__name {
  flex: 1;
  min-width: 0;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent__arrow {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: var(--color-muted);
}

/* -- Footer -- */
.landing__footer {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 24px 0;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.landing__footer-tagline {
  font-size: 0.875rem;
  color: var(--color-muted);
}

@keyframes hero-rise {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Static fallback when the observer never runs (reduced-motion seeds revealed
   immediately; this covers the brief pre-reveal frame and no-JS edge cases). */
@media (prefers-reduced-motion: reduce) {
  .feature {
    opacity: 1;
    transform: none;
    transition: none;
  }

  /* Reduced-motion users skip the intro overlay entirely; a brief opacity-only
     fade (no movement, safe under reduced-motion) softens the otherwise-instant
     page swap. Deliberately overrides the global blanket animation freeze, which
     would otherwise flatten even a plain cross-fade. */
  .landing {
    animation: landing-reduced-in 0.2s ease both !important;
  }
}

@keyframes landing-reduced-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* A two-column hero gets cramped below ~900px (constellation squeezed, copy
   wraps badly), so stack it well before phone widths. */
@media (max-width: 900px) {
  .hero {
    grid-template-columns: 1fr;
    gap: 24px;
    padding: 24px 0 48px;
    text-align: center;
  }

  .hero__subtitle {
    margin-inline: auto;
  }

  .hero__actions {
    justify-content: center;
  }

  .trust {
    justify-content: center;
  }

  .hero__visual {
    order: -1;
    max-width: 300px;
    margin: 0 auto;
  }

  /* The dot grid reads as fine texture on a wide hero but turns busy in a
     single narrow column — keep only the soft radial wash there. */
  .hero__bg {
    background-image: radial-gradient(
      70% 60% at 50% 28%,
      color-mix(in srgb, var(--color-accent) 10%, transparent),
      transparent 70%
    );
    background-size: auto;
    opacity: 0.7;
  }
}

@media (max-width: 768px) {
  .features,
  .recent__grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  /* The hero already provides a Log In button; drop the nav ghost link so the
     brand and primary CTA have breathing room on small phones. */
  .landing__nav-login {
    display: none;
  }
}
</style>
