package com.devmikeepr.fingerfight

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton

class HowToPlayActivity : AppCompatActivity() {

    private lateinit var glowBackground: GlowBackgroundView

    private data class Section(val iconRes: Int, val titleRes: Int, val bodyRes: Int)

    private val sections = listOf(
        Section(R.drawable.ic_bolt, R.string.htp_reaction_title, R.string.htp_reaction_body),
        Section(R.drawable.ic_bolt_group, R.string.htp_rumble_title, R.string.htp_rumble_body),
        Section(R.drawable.ic_tap, R.string.htp_tapbattle_title, R.string.htp_tapbattle_body),
        Section(R.drawable.ic_hourglass, R.string.htp_endurance_title, R.string.htp_endurance_body),
        Section(R.drawable.ic_touch_rings, R.string.htp_single_title, R.string.htp_single_body),
        Section(R.drawable.ic_touch_rings, R.string.htp_order_title, R.string.htp_order_body),
        Section(R.drawable.ic_touch_rings, R.string.htp_teamsplit_title, R.string.htp_teamsplit_body)
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_how_to_play)
        glowBackground = findViewById(R.id.glow_background)

        val container = findViewById<LinearLayout>(R.id.sections_container)
        val inflater = LayoutInflater.from(this)
        sections.forEachIndexed { index, section ->
            val item = inflater.inflate(R.layout.item_how_to_play_section, container, false)
            item.findViewById<ImageView>(R.id.icon).setImageResource(section.iconRes)
            item.findViewById<TextView>(R.id.title).setText(section.titleRes)
            val body = item.findViewById<TextView>(R.id.body).apply { setText(section.bodyRes) }
            val chevron = item.findViewById<ImageView>(R.id.chevron)
            val headerRow = item.findViewById<View>(R.id.header_row)

            var expanded = false
            headerRow.setOnClickListener {
                expanded = !expanded
                if (expanded) {
                    body.expand()
                    chevron.animate().rotation(180f).setDuration(200).start()
                } else {
                    body.collapse()
                    chevron.animate().rotation(0f).setDuration(200).start()
                }
            }

            container.addView(item)
            item.popIn(index * 40L)
        }

        findViewById<MaterialButton>(R.id.btn_close).setOnClickListener { finish() }
        findViewById<AdView>(R.id.ad_view).loadStandardAd()
    }

    override fun onResume() {
        super.onResume()
        glowBackground.startAnimating()
    }

    override fun onPause() {
        super.onPause()
        glowBackground.stopAnimating()
    }
}
