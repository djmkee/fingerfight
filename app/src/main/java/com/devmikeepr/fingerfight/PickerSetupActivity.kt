package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.google.android.gms.ads.AdView
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout

class PickerSetupActivity : AppCompatActivity() {

    private var pickType = PickType.SINGLE
    private var purpose = PickerPurpose.WHO_PAYS
    private lateinit var glowBackground: GlowBackgroundView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_picker_setup)

        glowBackground = findViewById(R.id.glow_background)
        val toggleType = findViewById<MaterialButtonToggleGroup>(R.id.toggle_pick_type)
        val sectionSingle = findViewById<View>(R.id.section_single)
        val sectionTitle = findViewById<View>(R.id.section_title)
        val chipGroup = findViewById<ChipGroup>(R.id.chip_group_purpose)
        val layoutCustomText = findViewById<TextInputLayout>(R.id.layout_custom_text)
        val editCustomText = findViewById<TextInputEditText>(R.id.edit_custom_text)
        val editListTitle = findViewById<TextInputEditText>(R.id.edit_list_title)
        val btnOrderPresetTurn = findViewById<MaterialButton>(R.id.btn_order_preset_turn)
        val btnOrderPresetDraft = findViewById<MaterialButton>(R.id.btn_order_preset_draft)
        val btnStart = findViewById<MaterialButton>(R.id.btn_start_picker)

        val knownPresets = setOf(
            getString(R.string.order_preset_turn),
            getString(R.string.order_preset_draft),
            getString(R.string.team_split_default_title)
        )

        toggleType.check(R.id.btn_pick_single)
        toggleType.addOnButtonCheckedListener { group, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
            group.hapticTick()
            pickType = when (checkedId) {
                R.id.btn_pick_single -> PickType.SINGLE
                R.id.btn_pick_order -> PickType.ORDER
                else -> PickType.TEAM_SPLIT
            }
            sectionSingle.visibility = if (pickType == PickType.SINGLE) View.VISIBLE else View.GONE
            sectionTitle.visibility = if (pickType == PickType.SINGLE) View.GONE else View.VISIBLE
            if (pickType == PickType.TEAM_SPLIT && (editListTitle.text?.toString() ?: "") in knownPresets) {
                editListTitle.setText(getString(R.string.team_split_default_title))
            } else if (pickType == PickType.ORDER && (editListTitle.text?.toString() ?: "") == getString(R.string.team_split_default_title)) {
                editListTitle.setText(getString(R.string.order_preset_turn))
            }
        }

        chipGroup.check(R.id.chip_who_pays)
        chipGroup.setOnCheckedChangeListener { group, checkedId ->
            group.hapticTick()
            purpose = when (checkedId) {
                R.id.chip_who_pays -> PickerPurpose.WHO_PAYS
                R.id.chip_whos_it -> PickerPurpose.WHOS_IT
                R.id.chip_goes_first -> PickerPurpose.GOES_FIRST
                R.id.chip_truth_or_dare -> PickerPurpose.TRUTH_OR_DARE
                R.id.chip_custom -> PickerPurpose.CUSTOM
                else -> PickerPurpose.WHO_PAYS
            }
            layoutCustomText.visibility = if (purpose == PickerPurpose.CUSTOM) View.VISIBLE else View.GONE
        }

        btnOrderPresetTurn.setOnClickListener { editListTitle.setText(getString(R.string.order_preset_turn)) }
        btnOrderPresetDraft.setOnClickListener { editListTitle.setText(getString(R.string.order_preset_draft)) }

        btnOrderPresetTurn.applyPressAnimation()
        btnOrderPresetDraft.applyPressAnimation()
        btnStart.applyPressAnimation()

        btnStart.setOnClickListener {
            val intent = Intent(this, PickerActivity::class.java)
            intent.putExtra(PickerActivity.EXTRA_PICK_TYPE, pickType.name)
            if (pickType == PickType.SINGLE) {
                intent.putExtra(PickerActivity.EXTRA_PURPOSE, purpose.name)
                if (purpose == PickerPurpose.CUSTOM) {
                    val customText = editCustomText.text?.toString()?.trim().orEmpty()
                    intent.putExtra(
                        PickerActivity.EXTRA_CUSTOM_TEXT,
                        customText.ifEmpty { getString(R.string.picker_custom_default) }
                    )
                }
            } else {
                val title = editListTitle.text?.toString()?.trim().orEmpty()
                val fallback = if (pickType == PickType.TEAM_SPLIT) {
                    getString(R.string.team_split_default_title)
                } else {
                    getString(R.string.order_preset_turn)
                }
                intent.putExtra(PickerActivity.EXTRA_LIST_TITLE, title.ifEmpty { fallback })
            }
            startActivity(intent)
        }

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
