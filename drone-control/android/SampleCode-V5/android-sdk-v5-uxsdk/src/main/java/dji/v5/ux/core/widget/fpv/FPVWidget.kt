/*
 * Copyright (c) 2018-2020 DJI
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 */
package dji.v5.ux.core.widget.fpv

import android.annotation.SuppressLint
import android.content.Context
import android.content.ContextWrapper
import android.app.Activity
import android.os.Looper
import android.os.SystemClock
import android.content.res.ColorStateList
import android.graphics.drawable.Drawable
import android.util.AttributeSet
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.widget.TextView
import androidx.annotation.ColorInt
import androidx.annotation.Dimension
import androidx.annotation.FloatRange
import androidx.annotation.StyleRes
import androidx.constraintlayout.widget.Guideline
import androidx.core.content.res.use
import dji.sdk.keyvalue.value.common.CameraLensType
import dji.sdk.keyvalue.value.common.ComponentIndexType
import dji.v5.manager.interfaces.ICameraStreamManager
import dji.v5.utils.common.DisplayUtil
import dji.v5.utils.common.LogPath
import dji.v5.utils.common.LogUtils
import dji.v5.ux.R
import dji.v5.ux.core.base.DJISDKModel
import dji.v5.ux.core.base.SchedulerProvider
import dji.v5.ux.core.base.widget.ConstraintLayoutWidget
import dji.v5.ux.core.communication.ObservableInMemoryKeyedStore
import dji.v5.ux.core.extension.*
import dji.v5.ux.core.module.FlatCameraModule
import dji.v5.ux.core.ui.CenterPointView
import dji.v5.ux.core.ui.GridLineView
import dji.v5.ux.core.util.UxErrorHandle
import dji.v5.ux.core.widget.fpv.FPVWidget.ModelState
import io.reactivex.rxjava3.core.Flowable

private const val TAG = "FPVWidget"
private const val ORIGINAL_SCALE = 1f
private const val LANDSCAPE_ROTATION_ANGLE = 0

/**
 * This widget shows the video feed from the camera.
 */
open class FPVWidget @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyleAttr: Int = 0
) : ConstraintLayoutWidget<ModelState>(context, attrs, defStyleAttr) {
    private var viewWidth = 0
    private var viewHeight = 0
    private var rotationAngle = 0
    private val surfacePolicy = SurfaceBindingPolicy()
    private val previewDiagnostics = PreviewDiagnostics { SystemClock.elapsedRealtime() }
    private var probeOwner: ICameraStreamManager? = null
    private var probeListener: ICameraStreamManager.CameraFrameListener? = null
    private var probeTimeout: Runnable? = null
    private var probeError: String? = null
    private var lastPreviewLogAt = -1L
    private val fpvSurfaceView: SurfaceView = findViewById(R.id.surface_view_fpv)
    private val cameraNameTextView: TextView = findViewById(R.id.textview_camera_name)
    private val cameraSideTextView: TextView = findViewById(R.id.textview_camera_side)
    private val verticalOffset: Guideline = findViewById(R.id.vertical_offset)
    private val horizontalOffset: Guideline = findViewById(R.id.horizontal_offset)
    private var fpvStateChangeResourceId: Int = INVALID_RESOURCE

    private val cameraSurfaceCallback = object : SurfaceHolder.Callback {
        override fun surfaceCreated(holder: SurfaceHolder) {
            onSurfaceThread {
                stopDecodedFrameProbe()
                if (surfacePolicy.surface !== holder.surface) removeSurfaceBinding()
                surfacePolicy.created(holder.surface)
                logPreview("created") // size is intentionally unknown until surfaceChanged
            }
        }

        override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            onSurfaceThread {
                if (surfacePolicy.surface !== holder.surface) removeSurfaceBinding()
                surfacePolicy.changed(holder.surface, width, height)
                updateCameraStream()
                logPreview("changed")
            }
        }

        override fun surfaceDestroyed(holder: SurfaceHolder) {
            onSurfaceThread {
                try { removeSurfaceBinding() } finally { surfacePolicy.destroy() }
                logPreview("destroyed")
            }
        }
    }

    val widgetModel: FPVWidgetModel = FPVWidgetModel(
        DJISDKModel.getInstance(), ObservableInMemoryKeyedStore.getInstance(), FlatCameraModule()
    )

    /**
     * Whether the video feed source's camera name is visible on the video feed.
     */
    var isCameraSourceNameVisible = true
        set(value) {
            field = value
            checkAndUpdateCameraName()
        }

    /**
     * Whether the video feed source's camera side is visible on the video feed.
     * Only shown on aircraft that support multiple gimbals.
     */
    var isCameraSourceSideVisible = true
        set(value) {
            field = value
            checkAndUpdateCameraSide()
        }

    /**
     * Whether the grid lines are enabled.
     */
    var isGridLinesEnabled = true
        set(isGridLinesEnabled) {
            field = isGridLinesEnabled
            updateGridLineVisibility()
        }

    /**
     * Whether the center point is enabled.
     */
    var isCenterPointEnabled = true
        set(isCenterPointEnabled) {
            field = isCenterPointEnabled
            centerPointView.visibility = if (isCenterPointEnabled) View.VISIBLE else View.GONE
        }

    /**
     * The text color state list of the camera name text view
     */
    var cameraNameTextColors: ColorStateList?
        get() = cameraNameTextView.textColors
        set(colorStateList) {
            cameraNameTextView.setTextColor(colorStateList)
        }

    /**
     * The text color of the camera name text view
     */
    @get:ColorInt
    @setparam:ColorInt
    var cameraNameTextColor: Int
        get() = cameraNameTextView.currentTextColor
        set(color) {
            cameraNameTextView.setTextColor(color)
        }

    /**
     * The text size of the camera name text view
     */
    @get:Dimension
    @setparam:Dimension
    var cameraNameTextSize: Float
        get() = cameraNameTextView.textSize
        set(textSize) {
            cameraNameTextView.textSize = textSize
        }

    /**
     * The background for the camera name text view
     */
    var cameraNameTextBackground: Drawable?
        get() = cameraNameTextView.background
        set(drawable) {
            cameraNameTextView.background = drawable
        }

    /**
     * The text color state list of the camera name text view
     */
    var cameraSideTextColors: ColorStateList?
        get() = cameraSideTextView.textColors
        set(colorStateList) {
            cameraSideTextView.setTextColor(colorStateList)
        }

    /**
     * The text color of the camera side text view
     */
    @get:ColorInt
    @setparam:ColorInt
    var cameraSideTextColor: Int
        get() = cameraSideTextView.currentTextColor
        set(color) {
            cameraSideTextView.setTextColor(color)
        }

    /**
     * The text size of the camera side text view
     */
    @get:Dimension
    @setparam:Dimension
    var cameraSideTextSize: Float
        get() = cameraSideTextView.textSize
        set(textSize) {
            cameraSideTextView.textSize = textSize
        }

    /**
     * The background for the camera side text view
     */
    var cameraSideTextBackground: Drawable?
        get() = cameraSideTextView.background
        set(drawable) {
            cameraSideTextView.background = drawable
        }

    /**
     * The vertical alignment of the camera name and side text views
     */
    var cameraDetailsVerticalAlignment: Float
        @FloatRange(from = 0.0, to = 1.0) get() {
            val layoutParams: LayoutParams = verticalOffset.layoutParams as LayoutParams
            return layoutParams.guidePercent
        }
        set(@FloatRange(from = 0.0, to = 1.0) percent) {
            val layoutParams: LayoutParams = verticalOffset.layoutParams as LayoutParams
            layoutParams.guidePercent = percent
            verticalOffset.layoutParams = layoutParams
        }

    /**
     * The horizontal alignment of the camera name and side text views
     */
    var cameraDetailsHorizontalAlignment: Float
        @FloatRange(from = 0.0, to = 1.0) get() {
            val layoutParams: LayoutParams = horizontalOffset.layoutParams as LayoutParams
            return layoutParams.guidePercent
        }
        set(@FloatRange(from = 0.0, to = 1.0) percent) {
            val layoutParams: LayoutParams = horizontalOffset.layoutParams as LayoutParams
            layoutParams.guidePercent = percent
            horizontalOffset.layoutParams = layoutParams
        }

    /**
     * The [GridLineView] shown in this widget
     */
    val gridLineView: GridLineView = findViewById(R.id.view_grid_line)

    /**
     * The [CenterPointView] shown in this widget
     */
    val centerPointView: CenterPointView = findViewById(R.id.view_center_point)

    //endregion

    //region Constructor
    override fun initView(context: Context, attrs: AttributeSet?, defStyleAttr: Int) {
        inflate(context, R.layout.uxsdk_widget_fpv, this)
    }

    init {
        if (!isInEditMode) {
            rotationAngle = LANDSCAPE_ROTATION_ANGLE
            fpvSurfaceView.holder.addCallback(cameraSurfaceCallback)
        }
        attrs?.let { initAttributes(context, it) }
    }
    //endregion

    //region LifeCycle
    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!isInEditMode) {
            surfacePolicy.attach()
            widgetModel.setup()
            val holder = fpvSurfaceView.holder
            if (holder.surface.isValid) {
                surfacePolicy.created(holder.surface)
                val size = holder.surfaceFrame
                if (size.width() > 0 && size.height() > 0) {
                    surfacePolicy.changed(holder.surface, size.width(), size.height())
                    updateCameraStream()
                }
            }
            logPreview("attached")
        }
        initializeListeners()
    }

    private fun initializeListeners() {
        //后面补上
    }

    override fun setVisibility(visibility: Int) {
        super.setVisibility(visibility)
        fpvSurfaceView.visibility = visibility
    }

    override fun onDetachedFromWindow() {
        destroyListeners()
        if (!isInEditMode) {
            surfacePolicy.detach() // invalidate posted updates and forget the destroyed Surface first
            try { removeSurfaceBinding() } finally { widgetModel.cleanup() }
            logPreview("detached")
        }
        super.onDetachedFromWindow()
    }

    override fun reactToModelChanges() {
        addReaction(widgetModel.displayMsgProcessor.toFlowable().observeOn(SchedulerProvider.ui()).subscribe { cameraName: String -> updateCameraName(cameraName) })
        addReaction(widgetModel.cameraSideProcessor.toFlowable().observeOn(SchedulerProvider.ui()).subscribe { cameraSide: String -> updateCameraSide(cameraSide) })
        addReaction(widgetModel.hasVideoViewChanged.observeOn(SchedulerProvider.ui()).subscribe {
            updateCameraStream()
        })
    }

    override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
        super.onLayout(changed, l, t, r, b)
        if (!isInEditMode) {
            setViewDimensions()
            delayCalculator()
        }
    }

    private fun destroyListeners() {
        //后面补上
    }

    //endregion
    //region Customization
    override fun getIdealDimensionRatioString(): String {
        return getString(R.string.uxsdk_widget_fpv_ratio)
    }

    fun updateVideoSource(source: ComponentIndexType) {
        onSurfaceThread {
            if (widgetModel.getCameraIndex() != source) stopDecodedFrameProbe()
            widgetModel.updateCameraSource(source, CameraLensType.UNKNOWN)
            surfacePolicy.allowRetry()
            updateCameraStream()
            if (source == ComponentIndexType.VISION_ASSIST) {
                widgetModel.enableVisionAssist()
            }
            fpvSurfaceView.invalidate()
        }
    }

    fun setOnFPVStreamSourceListener(listener: FPVStreamSourceListener) {
        widgetModel.streamSourceListener = listener
    }

    fun setSurfaceViewZOrderOnTop(onTop: Boolean) {
        fpvSurfaceView.setZOrderOnTop(onTop)
    }

    fun setSurfaceViewZOrderMediaOverlay(isMediaOverlay: Boolean) {
        fpvSurfaceView.setZOrderMediaOverlay(isMediaOverlay)
    }

    //endregion
    //region Helpers
    private fun setViewDimensions() {
        viewWidth = measuredWidth
        viewHeight = measuredHeight
    }

    private fun delayCalculator() {
        //后面补充
    }

    private fun updateCameraName(cameraName: String) {
        cameraNameTextView.text = cameraName
        if (cameraName.isNotEmpty() && isCameraSourceNameVisible) {
            cameraNameTextView.visibility = View.VISIBLE
        } else {
            cameraNameTextView.visibility = View.INVISIBLE
        }
    }

    private fun updateCameraSide(cameraSide: String) {
        cameraSideTextView.text = cameraSide
        if (cameraSide.isNotEmpty() && isCameraSourceSideVisible) {
            cameraSideTextView.visibility = View.VISIBLE
        } else {
            cameraSideTextView.visibility = View.INVISIBLE
        }
    }

    private fun checkAndUpdateCameraName() {
        if (!isInEditMode) {
            addDisposable(
                widgetModel.displayMsgProcessor.toFlowable().firstOrError().observeOn(SchedulerProvider.ui()).subscribe(
                    { cameraName: String -> updateCameraName(cameraName) }, UxErrorHandle.logErrorConsumer(TAG, "updateCameraName")
                )
            )
        }
    }

    private fun checkAndUpdateCameraSide() {
        if (!isInEditMode) {
            addDisposable(
                widgetModel.cameraSideProcessor.toFlowable().firstOrError().observeOn(SchedulerProvider.ui()).subscribe(
                    { cameraSide: String -> updateCameraSide(cameraSide) }, UxErrorHandle.logErrorConsumer(TAG, "updateCameraSide")
                )
            )
        }
    }

    private fun updateGridLineVisibility() {
        gridLineView.visibility = if (isGridLinesEnabled && widgetModel.getCameraIndex() == ComponentIndexType.FPV) View.VISIBLE else View.GONE
    }
    //endregion

    //region Customization helpers
    /**
     * Set text appearance of the camera name text view
     *
     * @param textAppearance Style resource for text appearance
     */
    fun setCameraNameTextAppearance(@StyleRes textAppearance: Int) {
        cameraNameTextView.setTextAppearance(context, textAppearance)
    }

    /**
     * Set text appearance of the camera side text view
     *
     * @param textAppearance Style resource for text appearance
     */
    fun setCameraSideTextAppearance(@StyleRes textAppearance: Int) {
        cameraSideTextView.setTextAppearance(context, textAppearance)
    }

    @SuppressLint("Recycle")
    private fun initAttributes(context: Context, attrs: AttributeSet) {
        context.obtainStyledAttributes(attrs, R.styleable.FPVWidget).use { typedArray ->
            if (!isInEditMode) {
                typedArray.getBooleanAndUse(R.styleable.FPVWidget_uxsdk_gridLinesEnabled, true) {
                    isGridLinesEnabled = it
                }
                typedArray.getBooleanAndUse(R.styleable.FPVWidget_uxsdk_centerPointEnabled, true) {
                    isCenterPointEnabled = it
                }
            }
            typedArray.getBooleanAndUse(R.styleable.FPVWidget_uxsdk_sourceCameraNameVisibility, true) {
                isCameraSourceNameVisible = it
            }
            typedArray.getBooleanAndUse(R.styleable.FPVWidget_uxsdk_sourceCameraSideVisibility, true) {
                isCameraSourceSideVisible = it
            }
            typedArray.getResourceIdAndUse(R.styleable.FPVWidget_uxsdk_cameraNameTextAppearance) {
                setCameraNameTextAppearance(it)
            }
            typedArray.getDimensionAndUse(R.styleable.FPVWidget_uxsdk_cameraNameTextSize) {
                cameraNameTextSize = DisplayUtil.pxToSp(context, it)
            }
            typedArray.getColorAndUse(R.styleable.FPVWidget_uxsdk_cameraNameTextColor) {
                cameraNameTextColor = it
            }
            typedArray.getDrawableAndUse(R.styleable.FPVWidget_uxsdk_cameraNameBackgroundDrawable) {
                cameraNameTextBackground = it
            }
            typedArray.getResourceIdAndUse(R.styleable.FPVWidget_uxsdk_cameraSideTextAppearance) {
                setCameraSideTextAppearance(it)
            }
            typedArray.getDimensionAndUse(R.styleable.FPVWidget_uxsdk_cameraSideTextSize) {
                cameraSideTextSize = DisplayUtil.pxToSp(context, it)
            }
            typedArray.getColorAndUse(R.styleable.FPVWidget_uxsdk_cameraSideTextColor) {
                cameraSideTextColor = it
            }
            typedArray.getDrawableAndUse(R.styleable.FPVWidget_uxsdk_cameraSideBackgroundDrawable) {
                cameraSideTextBackground = it
            }
            typedArray.getFloatAndUse(R.styleable.FPVWidget_uxsdk_cameraDetailsVerticalAlignment) {
                cameraDetailsVerticalAlignment = it
            }
            typedArray.getFloatAndUse(R.styleable.FPVWidget_uxsdk_cameraDetailsHorizontalAlignment) {
                cameraDetailsHorizontalAlignment = it
            }
            typedArray.getIntegerAndUse(R.styleable.FPVWidget_uxsdk_gridLineType) {
                gridLineView.type = GridLineView.GridLineType.find(it)
            }
            typedArray.getColorAndUse(R.styleable.FPVWidget_uxsdk_gridLineColor) {
                gridLineView.lineColor = it
            }
            typedArray.getFloatAndUse(R.styleable.FPVWidget_uxsdk_gridLineWidth) {
                gridLineView.lineWidth = it
            }
            typedArray.getIntegerAndUse(R.styleable.FPVWidget_uxsdk_gridLineNumber) {
                gridLineView.numberOfLines = it
            }
            typedArray.getIntegerAndUse(R.styleable.FPVWidget_uxsdk_centerPointType) {
                centerPointView.type = CenterPointView.CenterPointType.find(it)
            }
            typedArray.getColorAndUse(R.styleable.FPVWidget_uxsdk_centerPointColor) {
                centerPointView.color = it
            }
            typedArray.getResourceIdAndUse(R.styleable.FPVWidget_uxsdk_onStateChange) {
                fpvStateChangeResourceId = it
            }
        }
    }

    private fun updateCameraStream() {
        if (Looper.myLooper() != Looper.getMainLooper()) { onSurfaceThread { updateCameraStream() }; return }
        val surface = surfacePolicy.surface as? Surface ?: return
        val owner = widgetModel.currentStreamManager()
        val scale = ICameraStreamManager.ScaleType.CENTER_INSIDE
        val candidate = surfacePolicy.candidate(surface.isValid, owner, widgetModel.getCameraIndex().name, scale.name)
        if (candidate == null) {
            if (surfacePolicy.bound != null) removeSurfaceBinding()
            return
        }
        if (!surfacePolicy.needsPut(candidate, widgetModel.hasSurfaceBinding(surface, owner!!))) return
        if (probeOwner != null && probeOwner !== owner) stopDecodedFrameProbe()
        val success = widgetModel.tryPutCameraStreamSurface(surface, candidate.width, candidate.height, scale, owner)
        surfacePolicy.putResult(candidate, success)
        logPreview(if (success) "put_success" else "put_failed")
    }

    private fun removeSurfaceBinding() {
        try { stopDecodedFrameProbe() } finally {
            try { widgetModel.clearCameraStreamSurface() } finally { surfacePolicy.forgetBinding() }
        }
    }

    private fun onSurfaceThread(effect: () -> Unit) {
        val ticket = surfacePolicy.generation
        if (Looper.myLooper() == Looper.getMainLooper()) effect()
        else post { if (ticket == surfacePolicy.generation) effect() }
    }

    /** Optional diagnostics only. OFF by default; explicitly enabled probes stop within 30 seconds. */
    @JvmOverloads
    fun setDecodedFrameProbeEnabled(enabled: Boolean, durationMs: Long = 30000) {
        onSurfaceThread {
            stopDecodedFrameProbe()
            if (!enabled || !surfacePolicy.attached) return@onSurfaceThread
            val camera = widgetModel.getCameraIndex()
            if (camera != ComponentIndexType.LEFT_OR_MAIN && camera != ComponentIndexType.FPV) return@onSurfaceThread
            val owner = widgetModel.currentStreamManager() ?: return@onSurfaceThread
            val surface = surfacePolicy.surface as? Surface ?: return@onSurfaceThread
            if (!surface.isValid || !widgetModel.hasSurfaceBinding(surface, owner)) return@onSurfaceThread
            val duration = durationMs.coerceIn(1, 30000)
            val ticket = previewDiagnostics.startProbe(camera.name, duration)
            val listener = object : ICameraStreamManager.CameraFrameListener {
                override fun onFrame(frameData: ByteArray, offset: Int, length: Int, width: Int, height: Int,
                                     format: ICameraStreamManager.FrameFormat) {
                    // Counter-only callback; never copy or retain the SDK pixel array.
                    previewDiagnostics.frame(ticket, width, height, format.name)
                }
            }
            probeOwner = owner; probeListener = listener
            try {
                owner.addFrameListener(camera, ICameraStreamManager.FrameFormat.YUV420_888, listener)
                probeError = null
                val timeout = Runnable { stopDecodedFrameProbe(); logPreview("probe_timeout") }
                probeTimeout = timeout
                postDelayed(timeout, duration)
            } catch (error: RuntimeException) {
                probeError = "addFrameListener: ${error.javaClass.simpleName}: ${error.message}"
                stopDecodedFrameProbe()
            }
            logPreview("probe_requested")
        }
    }

    private fun stopDecodedFrameProbe() {
        previewDiagnostics.stopProbe() // old callbacks lose validity before SDK removal
        probeTimeout?.let { removeCallbacks(it) }; probeTimeout = null
        val owner = probeOwner; val listener = probeListener
        probeOwner = null; probeListener = null
        if (owner != null && listener != null) {
            try { owner.removeFrameListener(listener) }
            catch (error: RuntimeException) { probeError = "removeFrameListener: ${error.javaClass.simpleName}: ${error.message}" }
        }
    }

    /** Raw transport and PC decoding have independent diagnostics; put success does not prove phone pixels. */
    fun previewSnapshot(): Map<String, Any?> {
        var activityContext = context
        while (activityContext is ContextWrapper && activityContext !is Activity) {
            val next = activityContext.baseContext
            if (next === activityContext) break
            activityContext = next
        }
        return previewDiagnostics.snapshot() + widgetModel.surfaceDiagnostics() + linkedMapOf(
            "context" to context.javaClass.name, "activity" to activityContext.javaClass.name,
            "widget_identity" to System.identityHashCode(this), "widget_id" to id,
            "attached" to surfacePolicy.attached, "visibility" to visibility,
            "surface_generation" to surfacePolicy.generation,
            "surface_valid" to ((surfacePolicy.surface as? Surface)?.isValid ?: false),
            "surface_width" to surfacePolicy.width, "surface_height" to surfacePolicy.height,
            "surface_bound" to (surfacePolicy.bound?.let {
                widgetModel.hasSurfaceBinding(it.surface as Surface, it.manager as ICameraStreamManager)
            } ?: false), "camera" to widgetModel.getCameraIndex().name,
            "probe_error" to probeError)
    }

    private fun logPreview(event: String) {
        val now = SystemClock.elapsedRealtime()
        if (lastPreviewLogAt >= 0 && now - lastPreviewLogAt < 1000) return
        lastPreviewLogAt = now
        LogUtils.i(LogPath.SAMPLE, "FPVPreview event=$event ${previewSnapshot()}")
    }

    /**
     * Get the [ModelState] updates
     */
    @SuppressWarnings
    override fun getWidgetStateUpdate(): Flowable<ModelState> {
        return super.getWidgetStateUpdate()
    }

    /**
     * Class defines the widget state updates
     */
    sealed class ModelState
}
