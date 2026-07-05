// Canvas 2D particle-field engine behind the landing constellation. Kept as a
// plain module (no Vue) so the render loop, physics, and theming are unit-sized
// and the component wrapper stays a thin lifecycle adapter.
//
// Restraint is the design constraint: few, tiny, dim particles whose proximity
// links fade out away from the canvas centre, so the field reads as ambient
// depth around the constellation rather than a full-bleed template effect.

export interface ParticleFieldOptions {
  count: number
}

export interface ParticleFieldHandle {
  start(): void
  stop(): void
  resize(): void
  readTheme(): void
  setPointer(x: number, y: number): void
  clearPointer(): void
  destroy(): void
}

interface Particle {
  // Home position in CSS pixels (drift updates these).
  x: number
  y: number
  vx: number
  vy: number
  r: number
  // Per-particle base alpha, so the field shimmers instead of reading flat.
  a: number
}

// Link two particles only when closer than this (CSS px).
const LINK_DIST = 96
// Pointer repulsion radius / maximum displacement (CSS px).
const POINTER_RADIUS = 130
const POINTER_PUSH = 26
// Cap the physics step so a background-tab resume cannot teleport particles.
const MAX_DT = 0.05
const DPR_CAP = 2

// `--color-accent` is a hex token in both themes; parse defensively and fall
// back to the light-theme accent so a token rename degrades softly.
function parseHex(raw: string): { r: number; g: number; b: number } {
  const hex = raw.trim().replace('#', '')
  const full =
    hex.length === 3
      ? hex
          .split('')
          .map((c) => c + c)
          .join('')
      : hex
  const n = Number.parseInt(full, 16)
  if (full.length !== 6 || Number.isNaN(n)) return { r: 37, g: 99, b: 235 }
  return { r: (n >> 16) & 0xff, g: (n >> 8) & 0xff, b: n & 0xff }
}

export function createParticleField(
  canvas: HTMLCanvasElement,
  ctx: CanvasRenderingContext2D,
  options: ParticleFieldOptions,
): ParticleFieldHandle {
  let width = 0
  let height = 0
  let color = parseHex('')
  let rafId = 0
  let lastTime = 0
  let pointer: { x: number; y: number } | null = null
  let particles: Particle[] = []
  // Displaced draw positions, recomputed each frame from home + pointer repulsion.
  let drawX: number[] = []
  let drawY: number[] = []

  function seed(): void {
    particles = Array.from({ length: options.count }, () => {
      // Sum of two uniforms biases spawn density toward the canvas centre,
      // where the constellation sits.
      const bx = (Math.random() + Math.random()) / 2
      const by = (Math.random() + Math.random()) / 2
      const angle = Math.random() * Math.PI * 2
      const speed = 4 + Math.random() * 7
      return {
        x: bx * width,
        y: by * height,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        r: 0.7 + Math.random() * 1.1,
        a: 0.35 + Math.random() * 0.45,
      }
    })
    drawX = new Array(particles.length).fill(0)
    drawY = new Array(particles.length).fill(0)
  }

  function resize(): void {
    const rect = canvas.getBoundingClientRect()
    width = rect.width
    height = rect.height
    const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP)
    canvas.width = Math.max(1, Math.round(width * dpr))
    canvas.height = Math.max(1, Math.round(height * dpr))
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    if (!particles.length && width && height) seed()
  }

  function readTheme(): void {
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--color-accent')
    color = parseHex(raw)
  }

  // Opacity falloff from the canvas centre, so links and particles fade out
  // toward the edges instead of tiling the whole hero.
  function centerFade(x: number, y: number): number {
    const half = Math.min(width, height) * 0.62
    if (half <= 0) return 0
    const d = Math.hypot(x - width / 2, y - height / 2)
    return Math.max(0, 1 - d / half)
  }

  function step(dt: number): void {
    for (const p of particles) {
      p.x += p.vx * dt
      p.y += p.vy * dt
      // Wrap with a margin so particles glide off one edge and back in the
      // other instead of popping.
      if (p.x < -8) p.x = width + 8
      else if (p.x > width + 8) p.x = -8
      if (p.y < -8) p.y = height + 8
      else if (p.y > height + 8) p.y = -8
    }
  }

  function draw(): void {
    ctx.clearRect(0, 0, width, height)
    const { r, g, b } = color

    for (let i = 0; i < particles.length; i++) {
      const p = particles[i]
      let ox = 0
      let oy = 0
      if (pointer) {
        const dx = p.x - pointer.x
        const dy = p.y - pointer.y
        const d = Math.hypot(dx, dy)
        if (d < POINTER_RADIUS && d > 0.001) {
          const f = (1 - d / POINTER_RADIUS) * POINTER_PUSH
          ox = (dx / d) * f
          oy = (dy / d) * f
        }
      }
      drawX[i] = p.x + ox
      drawY[i] = p.y + oy
    }

    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = drawX[i] - drawX[j]
        const dy = drawY[i] - drawY[j]
        const d = Math.hypot(dx, dy)
        if (d >= LINK_DIST) continue
        const mx = (drawX[i] + drawX[j]) / 2
        const my = (drawY[i] + drawY[j]) / 2
        const alpha = (1 - d / LINK_DIST) * centerFade(mx, my) * 0.28
        if (alpha <= 0.01) continue
        ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(drawX[i], drawY[i])
        ctx.lineTo(drawX[j], drawY[j])
        ctx.stroke()
      }
    }

    for (let i = 0; i < particles.length; i++) {
      const p = particles[i]
      const alpha = p.a * (0.35 + 0.65 * centerFade(drawX[i], drawY[i]))
      if (alpha <= 0.02) continue
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
      ctx.beginPath()
      ctx.arc(drawX[i], drawY[i], p.r, 0, Math.PI * 2)
      ctx.fill()
    }
  }

  function frame(now: number): void {
    rafId = requestAnimationFrame(frame)
    const dt = lastTime ? Math.min((now - lastTime) / 1000, MAX_DT) : 0
    lastTime = now
    if (!width || !height) return
    step(dt)
    draw()
  }

  return {
    start(): void {
      if (rafId) return
      lastTime = 0
      rafId = requestAnimationFrame(frame)
    },
    stop(): void {
      if (!rafId) return
      cancelAnimationFrame(rafId)
      rafId = 0
    },
    resize,
    readTheme,
    setPointer(x: number, y: number): void {
      pointer = { x, y }
    },
    clearPointer(): void {
      pointer = null
    },
    destroy(): void {
      if (rafId) cancelAnimationFrame(rafId)
      rafId = 0
      particles = []
    },
  }
}
