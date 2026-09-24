package com.devmikeepr.fingerfight

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.sin
import kotlin.random.Random

/**
 * Decorative animated backdrop: a static diagonal gradient with a handful of
 * soft glowing orbs that drift slowly. Purely a Canvas effect -- no images,
 * no extra dependencies.
 */
class GlowBackgroundView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private class Orb(val baseX: Float, val baseY: Float, val radius: Float, val color: Int, val phase: Float, val speed: Float)

    private val backgroundPaint = Paint()
    private val orbPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val orbs = mutableListOf<Orb>()
    private var animatedT = 0f
    private var animator: ValueAnimator? = null

    init {
        setLayerType(LAYER_TYPE_SOFTWARE, null)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w == 0 || h == 0) return

        backgroundPaint.shader = LinearGradient(
            0f, 0f, w.toFloat(), h.toFloat(),
            context.getColor(R.color.bg_gradient_top),
            context.getColor(R.color.bg_gradient_bottom),
            Shader.TileMode.CLAMP
        )

        if (orbs.isEmpty()) {
            val rnd = Random(7)
            val palette = intArrayOf(
                context.getColor(R.color.glow_accent_1),
                context.getColor(R.color.glow_accent_2),
                context.getColor(R.color.glow_accent_3)
            )
            repeat(4) { i ->
                orbs.add(
                    Orb(
                        baseX = rnd.nextFloat() * w,
                        baseY = rnd.nextFloat() * h,
                        radius = w * (0.20f + rnd.nextFloat() * 0.12f),
                        color = palette[i % palette.size],
                        phase = rnd.nextFloat() * 6.28f,
                        speed = 0.4f + rnd.nextFloat() * 0.3f
                    )
                )
            }
        }
    }

    fun startAnimating() {
        if (animator != null) return
        animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 14000
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener {
                animatedT = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    fun stopAnimating() {
        animator?.cancel()
        animator = null
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), backgroundPaint)

        val t = animatedT * 2f * Math.PI.toFloat()
        for (orb in orbs) {
            val dx = sin(t * orb.speed + orb.phase) * width * 0.10f
            val dy = sin(t * orb.speed * 0.7f + orb.phase * 1.3f) * height * 0.07f
            val cx = orb.baseX + dx
            val cy = orb.baseY + dy
            orbPaint.shader = RadialGradient(
                cx, cy, orb.radius,
                intArrayOf(orb.color, orb.color and 0x00FFFFFF),
                floatArrayOf(0f, 1f),
                Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, orb.radius, orbPaint)
        }
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        stopAnimating()
    }
}
