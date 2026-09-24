package com.devmikeepr.fingerfight

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout

class PickerSetupActivity : AppCompatActivity() {

    private var pickType = PickType.SINGLE
    private var purpose = PickerPurpose.WHO_PAYS

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_picker_setup)

        val toggleType = findViewById<MaterialButtonToggleGroup>(R.id.toggle_pick_type)
        val sectionSingle = findViewById<View>(R.id.section_single)
        val sectionOrder = findViewById<View>(R.id.section_order)
        val chipGroup = findViewById<ChipGroup>(R.id.chip_group_purpose)
        val layoutCustomText = findViewById<TextInputLayout>(R.id.layout_custom_text)
        val editCustomText = findViewById<TextInputEditText>(R.id.edit_custom_text)
        val editOrderTitle = findViewById<TextInputEditText>(R.id.edit_order_title)
        val btnOrderPresetTurn = findViewById<MaterialButton>(R.id.btn_order_preset_turn)
        val btnOrderPresetDraft = findViewById<MaterialButton>(R.id.btn_order_preset_draft)
        val btnStart = findViewById<MaterialButton>(R.id.btn_start_picker)

        toggleType.check(R.id.btn_pick_single)
        toggleType.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
            pickType = if (checkedId == R.id.btn_pick_single) PickType.SINGLE else PickType.ORDER
            sectionSingle.visibility = if (pickType == PickType.SINGLE) View.VISIBLE else View.GONE
            sectionOrder.visibility = if (pickType == PickType.ORDER) View.VISIBLE else View.GONE
        }

        chipGroup.check(R.id.chip_who_pays)
        chipGroup.setOnCheckedChangeListener { _, checkedId ->
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

        btnOrderPresetTurn.setOnClickListener { editOrderTitle.setText(getString(R.string.order_preset_turn)) }
        btnOrderPresetDraft.setOnClickListener { editOrderTitle.setText(getString(R.string.order_preset_draft)) }

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
                val title = editOrderTitle.text?.toString()?.trim().orEmpty()
                intent.putExtra(
                    PickerActivity.EXTRA_ORDER_TITLE,
                    title.ifEmpty { getString(R.string.order_preset_turn) }
                )
            }
            startActivity(intent)
        }
    }
}
