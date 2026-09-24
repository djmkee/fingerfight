package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.view.View
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

abstract class BaseArenaView(context: Context) : View(context) {

    var listener: ArenaListener? = null
    var soundManager: SoundManager? = null

    private val density = resources.displayMetrics.density
    protected fun dp(value: Float): Float = value * density

    init {
        // setShadowLayer() needs a software-rendered canvas to produce a real
        // blur on every API level this app supports.
        setLayerType(LAYER_TYPE_SOFTWARE, null)
    }

    private val circlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    protected val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(4f)
    }

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        typeface = Typeface.DEFAULT_BOLD
    }

    protected val messagePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        typeface = Typeface.DEFAULT_BOLD
        setShadowLayer(dp(12f), 0f, 0f, 0xAA00E5FF.toInt())
    }

    protected fun drawPlayerCircle(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        color: Int,
        label: String,
        alpha: Float = 1f
    ) {
        val a = (255 * alpha).toInt().coerceIn(0, 255)
        circlePaint.color = color
        circlePaint.alpha = a
        circlePaint.setShadowLayer(radius * 0.7f, 0f, 0f, color)
        canvas.drawCircle(cx, cy, radius, circlePaint)

        labelPaint.textSize = radius * 0.9f
        labelPaint.alpha = a
        val labelY = cy - (labelPaint.descent() + labelPaint.ascent()) / 2
        canvas.drawText(label, cx, labelY, labelPaint)
    }

    // ---- Touch-down ripple effect (shared by every arena) ----

    private class Ripple(val x: Float, val y: Float, val color: Int, val startTime: Long)

    private val ripples = mutableListOf<Ripple>()
    private val ripplePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

    protected fun spawnRipple(x: Float, y: Float, color: Int) {
        ripples.add(Ripple(x, y, color, System.currentTimeMillis()))
        if (ripples.size > 12) ripples.removeAt(0)
        invalidate()
    }

    protected fun drawRipples(canvas: Canvas) {
        if (ripples.isEmpty()) return
        val now = System.currentTimeMillis()
        val iterator = ripples.iterator()
        while (iterator.hasNext()) {
            val r = iterator.next()
            val elapsed = now - r.startTime
            if (elapsed > RIPPLE_DURATION_MS) {
                iterator.remove()
                continue
            }
            val progress = elapsed / RIPPLE_DURATION_MS.toFloat()
            ripplePaint.color = r.color
            ripplePaint.alpha = (255 * (1f - progress)).toInt().coerceIn(0, 255)
            ripplePaint.strokeWidth = dp(3f)
            canvas.drawCircle(r.x, r.y, dp(18f) + progress * dp(55f), ripplePaint)
        }
        invalidate()
    }

    // ---- Confetti burst effect (used on a celebratory reveal) ----

    private class Confetti(var x: Float, var y: Float, var vx: Float, var vy: Float, val color: Int, var life: Float)

    private val confetti = mutableListOf<Confetti>()
    private val confettiPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var lastConfettiFrameTime = 0L

    protected fun spawnConfettiBurst(x: Float, y: Float) {
        val now = System.currentTimeMillis()
        lastConfettiFrameTime = now
        repeat(24) {
            val angle = Random.nextFloat() * 2f * Math.PI.toFloat()
            val speed = dp(2f) + Random.nextFloat() * dp(4f)
            confetti.add(
                Confetti(
                    x = x,
                    y = y,
                    vx = cos(angle) * speed,
                    vy = sin(angle) * speed - dp(2f),
                    color = ColorPalette.colorFor(Random.nextInt(8)),
                    life = 1f
                )
            )
        }
        invalidate()
    }

    protected fun drawConfetti(canvas: Canvas) {
        if (confetti.isEmpty()) return
        val now = System.currentTimeMillis()
        val dt = (now - lastConfettiFrameTime).coerceIn(1L, 48L) / 16f
        lastConfettiFrameTime = now
        val gravity = dp(0.15f)
        val iterator = confetti.iterator()
        while (iterator.hasNext()) {
            val c = iterator.next()
            c.vy += gravity * dt
            c.x += c.vx * dt
            c.y += c.vy * dt
            c.life -= 0.02f * dt
            if (c.life <= 0f) {
                iterator.remove()
                continue
            }
            confettiPaint.color = c.color
            confettiPaint.alpha = (255 * c.life).toInt().coerceIn(0, 255)
            canvas.drawRect(c.x - dp(3f), c.y - dp(3f), c.x + dp(3f), c.y + dp(3f), confettiPaint)
        }
        invalidate()
    }

    abstract fun startRound()
    abstract fun cleanup()

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        cleanup()
    }

    companion object {
        private const val RIPPLE_DURATION_MS = 500L
    }
}
