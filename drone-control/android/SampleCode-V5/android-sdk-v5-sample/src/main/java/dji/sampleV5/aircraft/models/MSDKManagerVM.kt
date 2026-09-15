package dji.sampleV5.aircraft.models

import android.content.Context
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import dji.v5.common.error.IDJIError
import dji.v5.common.register.DJISDKInitEvent
import dji.v5.manager.SDKManager
import dji.v5.manager.interfaces.SDKManagerCallback
import dji.v5.manager.ldm.LDMManager
import dji.v5.network.DJINetworkManager
import dji.v5.utils.common.ContextUtil
import com.msdkremote.diagnostics.FieldDiagnostics

class MSDKManagerVM : ViewModel() {
    // The data is held in livedata mode, but you can also save the results of the sdk callbacks any way you like.
    val lvRegisterState = MutableLiveData<Pair<Boolean, IDJIError?>>()
    val lvProductConnectionState = MutableLiveData<Pair<Boolean, Int>>()
    val lvProductChanges = MutableLiveData<Int>()
    val lvInitProcess = MutableLiveData<Pair<DJISDKInitEvent, Int>>()
    val lvDBDownloadProgress = MutableLiveData<Pair<Long, Long>>()
    var isInit = false

    fun initMobileSDK(appContext: Context) {
        FieldDiagnostics.start(appContext)
        // Android reclaims a backgrounded process and sleeps its radios; the TCP servers die with it.
        com.msdkremote.lifecycle.BridgeForegroundService.start(appContext)
        FieldDiagnostics.callSite("sdk_init_requested")
        // Initialize and set the sdk callback, which is held internally by the sdk until destroy() is called
        SDKManager.getInstance().init(appContext, object : SDKManagerCallback {
            override fun onRegisterSuccess() {
                FieldDiagnostics.event("sdk_registered", emptyMap())
                lvRegisterState.postValue(Pair(true, null))
                // PC bridge: TCP servers for control, video and key queries.
                com.msdkremote.PcBridge.start(dji.sampleV5.aircraft.BuildConfig.OPERATOR_ARM_TOKEN)
            }

            override fun onRegisterFailure(error: IDJIError) {
                FieldDiagnostics.event("sdk_registration_failed", mapOf("error_code" to error.errorCode()))
                lvRegisterState.postValue(Pair(false, error))
            }

            override fun onProductDisconnect(productId: Int) {
                FieldDiagnostics.event("sdk_product_disconnected", mapOf("product_id" to productId))
                lvProductConnectionState.postValue(Pair(false, productId))
                com.msdkremote.PcBridge.onProductDisconnected(productId)
            }

            override fun onProductConnect(productId: Int) {
                FieldDiagnostics.event("sdk_product_connected", mapOf("product_id" to productId))
                lvProductConnectionState.postValue(Pair(true, productId))
                com.msdkremote.PcBridge.onProductConnected(productId)
            }

            override fun onProductChanged(productId: Int) {
                FieldDiagnostics.event("sdk_product_changed", mapOf("product_id" to productId))
                lvProductChanges.postValue(productId)
                com.msdkremote.PcBridge.onProductChanged(productId)
            }

            override fun onInitProcess(event: DJISDKInitEvent, totalProcess: Int) {
                FieldDiagnostics.event("sdk_init_progress", mapOf("event" to event.name, "progress" to totalProcess))
                lvInitProcess.postValue(Pair(event, totalProcess))
                // Don't forget to call the registerApp()
                if (event == DJISDKInitEvent.INITIALIZE_COMPLETE) {
                    isInit = true
                    FieldDiagnostics.event("sdk_register_requested", mapOf("source" to "initialize_complete"))
                    SDKManager.getInstance().registerApp()
                }
            }

            override fun onDatabaseDownloadProgress(current: Long, total: Long) {
                lvDBDownloadProgress.postValue(Pair(current, total))
            }
        })

//        LDMManager.getInstance().enableLDM(ContextUtil.getContext(),null)

        DJINetworkManager.getInstance().addNetworkStatusListener { isAvailable ->
            FieldDiagnostics.event("sdk_network_status", mapOf("available" to isAvailable,
                "initialized" to isInit, "registered" to SDKManager.getInstance().isRegistered))
            if (isInit && isAvailable && !SDKManager.getInstance().isRegistered) {
                FieldDiagnostics.event("sdk_register_requested", mapOf("source" to "network_available"))
                SDKManager.getInstance().registerApp()
            }
        }
    }

    fun destroyMobileSDK() {
        FieldDiagnostics.callSite("sdk_destroy_requested")
        SDKManager.getInstance().destroy()
        FieldDiagnostics.event("sdk_destroy_returned", emptyMap())
    }

}
