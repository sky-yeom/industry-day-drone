package com.msdkremote.livequery;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.msdkremote.commandserver.CommandServer;

import dji.sdk.keyvalue.converter.DJIValueConverter;
import dji.sdk.keyvalue.converter.IDJIValueConverter;
import dji.sdk.keyvalue.key.DJIActionKeyInfo;
import dji.sdk.keyvalue.key.DJIKey;
import dji.sdk.keyvalue.key.DJIKeyInfo;
import dji.sdk.keyvalue.key.FlightControllerKey;
import dji.sdk.keyvalue.key.ProductKey;
import dji.sdk.keyvalue.key.RemoteControllerKey;
import dji.sdk.keyvalue.value.common.EmptyMsg;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;

public class KeyItem<Param, Result>
{
    // Whenever the operation is successful without specific output,
    // return this string to signal success.
    public static final String SUCCESS_MESSAGE = "success";

    // Whenever the operator needed a parameter, and the cast didn't succeed,
    // return this string to show the operation wasn't made.
    public static final String UNSUCCESSFUL_CAST = "could not cast parameter";

    @NonNull private final String moduleName;
    @NonNull private final String keyName;

    @NonNull private final DJIKeyInfo<Param> keyInfo;
    @Nullable private final DJIActionKeyInfo<Param, Result> ActionKeyInfo;


    /**
     * Construct new KeyItem.
     *
     * @param keyInfo the DJIKeyInfo this item represents.
     * @param moduleName the module associated with this key.
     */
    public KeyItem(@NonNull DJIKeyInfo<Param> keyInfo, @NonNull String moduleName)
    {
        this.keyInfo = keyInfo;
        this.moduleName = moduleName;
        this.keyName = keyInfo.getIdentifier();

        if (keyInfo instanceof DJIActionKeyInfo)
            this.ActionKeyInfo = (DJIActionKeyInfo<Param, Result>) keyInfo;

        else
            this.ActionKeyInfo = null;
    }


    /**
     * Get the associated module name for this key.
     *
     * @return string representing the module name.
     */
    @NonNull
    public String getModuleName() {
        return this.moduleName;
    }


    /**
     * Get the name of this key.
     *
     * @return string representing the key.
     */
    @NonNull
    public String getKeyName() {
        return this.keyName;
    }


    /**
     * Get the representing name of this key, used for notating the key.
     * Mainly used internally to use with the CommandServer.
     * <br/>
     * {@code moduleName} + " " + {@code keyName}
     *
     * @return unique string representing this {@code KeyInfo}.
     */
    @NonNull
    public String getPresentingName() {
        return this.moduleName + " " + this.keyName;
    }


    /**
     * Get the raw {@code DJIKeyInfo} of this {@code KeyInfo}.
     *
     * @return {@code DJIKeyInfo} making this {@code KeyInfo}
     */
    @NonNull
    public DJIKeyInfo<Param> getRawKeyInfo() {
        return this.keyInfo;
    }


    /**
     * Get the raw {@code DJIActionKeyInfo} of this {@code KeyInfo},
     * if this {@code KeyInfo} is created by action key,
     * else this method will return {@code null}.
     *
     * @return {@code DJIActionKeyInfo} of this InfoKey, or null.
     */
    @Nullable
    public DJIActionKeyInfo<Param, Result> getRawActionKeyInfo() {
        return this.ActionKeyInfo;
    }


    /**
     * Send message on associated CommandServer, with this KeyInfo identifier.
     *
     * @param server server of the communication.
     * @param message the string to send.
     */
    private void sendMessage(@NonNull CommandServer server, @NonNull String message) {
        server.sendMessage(this.getPresentingName() + " " + message);
    }


    /**
     * Send message on associated CommandServer, represented as object.
     *
     * @param server server of the communication.
     * @param objectMessage the object to send.
     */
    private void sendMessage(@NonNull CommandServer server, @Nullable Object objectMessage)
    {
        if (objectMessage == null)
            sendMessage(server, "null");
        else
            sendMessage(server, objectMessage.toString());
    }


    /**
     * Convert string to parameter. <br>
     * Supports classes, enums and Java types.
     *
     * @param parameter the parameter, in string format.
     * @return return the parameter in its raw format, or null if cast failed.
     */
    @Nullable
    public Param getParameter(@NonNull String parameter)
    {
        // If no TypeConverter is presented, notting to do.
        if (getRawKeyInfo().getTypeConverter() == null)
            return null;

        // Get class of parameter
        Class<?> clazz = getRawKeyInfo().getTypeConverter().getClassType();

        // If no Class<Param>, notting to do.
        if (clazz == null)
            return null;

        // If enum, find value by key.
        if (clazz.isEnum())
        {
            // Get all enums in this class
            Object[] enums = clazz.getEnumConstants();
            if (enums == null)
                return null;

            // Check if one of them match its name.
            for (Object enumObj : enums)
            {
                // If this is not enum type, notting to do.
                if (!(enumObj instanceof Enum))
                    continue;

                // Check if name matching
                if (((Enum<?>) enumObj).name().equals(parameter))
                    return (Param) enumObj;
            }
        }

        // If Java type, cast by string.
        // If general class, just cast.
        return (Param) getRawKeyInfo().getTypeConverter().fromStr(parameter);

        /*
         * Sadly, the casting is unchecked, e.g. (Param) obj is valid,
         * even if obj is not instance of Param. However, this should not be limitation,
         * as in Java, the generic type is only for convenience, and no casting error
         * will be thrown. Also, we at most call the toString() method, which present
         * in any object. Sadly, I have no clue how th DJI package handle the objects,
         * as it is obfuscated. Hopefully they didn't mismatch any type, and with all
         * my tests and examples, I didn't find one.
         */
    }


    /**
     * Get string representing this KeyItem object.
     *
     * @return string with the module name, key name,
     *         and the string representing DJIKeyInfo.
     */
    @NonNull
    @Override
    public String toString() {
        return "KeyItem{" +
                "moduleName='" + moduleName + '\'' +
                ", keyName='" + keyName + '\'' +
                ", keyInfo=" + keyInfo +
                '}';
    }
}
