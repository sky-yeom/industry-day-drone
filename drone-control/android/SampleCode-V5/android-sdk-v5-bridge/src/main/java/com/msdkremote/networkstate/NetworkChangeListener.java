package com.msdkremote.networkstate;

import androidx.annotation.Nullable;

import java.net.InetAddress;

public interface NetworkChangeListener
{
    /**
     * Network state listener - will be called each time the network state is changed.
     *
     * @param networkType the type of connected network.
     * @param address the IPv4 address of the current network, or null if the
     *                network has no usable address yet.
     */
    void onNetworkChange (NetworkType networkType, @Nullable InetAddress address);
}
