package com.devmikeepr.fingerfight

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.view.View

abstract class BaseArenaView(context: Context) : View(context) {

    var listener: ArenaListener? = null
    var soundManager: SoundManager? = null

    private val density = resources.displayMetrics.density
    protected fun dp(value: Float): Float = value * density

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
        canvas.drawCircle(cx, cy, radius, circlePaint)

        labelPaint.textSize = radius * 0.9f
        labelPaint.alpha = a
        val labelY = cy - (labelPaint.descent() + labelPaint.ascent()) / 2
        canvas.drawText(label, cx, labelY, labelPaint)
    }

    abstract fun startRound()
    abstract fun cleanup()

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        cleanup()
    }
}
