package com.msdkremote.networkstate;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.util.LinkedHashSet;
import java.util.Set;

public class NetworkMonitor
{
    // Set for all the listeners on this monitor
    private final Set<NetworkChangeListener> listeners = new LinkedHashSet<>();

    // Connectivity manager - to get the connection type and IP address
    private final ConnectivityManager connectivityManager;


    /**
     * Construct NetworkMonitor that will watch on network state changes,
     * and will notify listeners of current network type and IP address.
     *
     * @param context context of the application.
     */
    public NetworkMonitor(Context context)
    {
        // Get ConnectivityManager for future use
        connectivityManager = (ConnectivityManager)
                context.getSystemService(Context.CONNECTIVITY_SERVICE);

        // Register network state listener, which in his turn notify its listeners.
        connectivityManager.registerNetworkCallback(
                new NetworkRequest.Builder().build(),
                new ConnectivityManager.NetworkCallback() {
                    @Override public void onAvailable(@NonNull Network network) { notifyListeners(); }
                    @Override public void onLost(@NonNull Network network) { notifyListeners(); }

                    // The address usually arrives after onAvailable, so
                    // without this the display can stay stuck on "---".
                    @Override
                    public void onLinkPropertiesChanged(
                            @NonNull Network network, @NonNull LinkProperties properties) {
                        notifyListeners();
                    }
                }
        );
    }


    /**
     * Register network listener that will be called on network state change.
     * Will be called upon registration with latest data.
     *
     * @param listener the listener to add.
     */
    public synchronized void registerListener(NetworkChangeListener listener)
    {
        listeners.add(listener);

        // Inform the listener with current information
        listener.onNetworkChange(getCurrentNetworkType(), getIPAddress());
    }


    /**
     * Remove specific network listener from this network monitor.
     *
     * @param listener the listener to remove.
     */
    public synchronized void unregisterListener(NetworkChangeListener listener) {
        listeners.remove(listener);
    }


    /**
     * Remove all the network listeners from this network monitor.
     */
    public synchronized void removeAllListeners() {
        listeners.clear();
    }


    /**
     * Inner method - notifying all the listeners.
     */
    private synchronized void notifyListeners()
    {
        NetworkType networkType = getCurrentNetworkType();
        InetAddress address = getIPAddress();

        for (NetworkChangeListener listener : listeners) {
            listener.onNetworkChange(networkType, address);
        }
    }

    /**
     * Inner method - get current network type.
     *
     * @return current network connection type.
     */
    private NetworkType getCurrentNetworkType()
    {
        // Get current network state
        NetworkCapabilities capabilities =
                connectivityManager.getNetworkCapabilities(connectivityManager.getActiveNetwork());

        // No network connection presents
        if (capabilities == null)
            return NetworkType.NETWORK_DISCONNECTED;

        // Check if network connected to wifi
        else if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI))
            return NetworkType.NETWORK_WIFI;

        // Check if network connected to mobile network
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR))
            return NetworkType.NETWORK_MOBILE;

        // Unknown capabilities
        return NetworkType.NETWORK_UNKNOWN;
    }


    /**
     * Inner method - get the active network's IPv4 address.
     * <p>
     * {@code WifiManager.getConnectionInfo()} is deprecated and returns a
     * zeroed {@code WifiInfo} on Android 13+ unless the app holds
     * {@code ACCESS_FINE_LOCATION}, which would have shown 0.0.0.0 forever
     * on this phone. Link properties need no permission.
     *
     * @return the IPv4 address, or null if there is no usable network.
     */
    @Nullable
    private InetAddress getIPAddress()
    {
        Network network = connectivityManager.getActiveNetwork();
        if (network == null)
            return null;

        LinkProperties properties = connectivityManager.getLinkProperties(network);
        if (properties == null)
            return null;

        for (LinkAddress linkAddress : properties.getLinkAddresses())
        {
            InetAddress address = linkAddress.getAddress();

            if (address instanceof Inet4Address
                    && !address.isLoopbackAddress()
                    && !address.isAnyLocalAddress())
            {
                return address;
            }
        }

        return null;
    }
}
