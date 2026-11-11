<?php

function plugin_aiassistant_install(): bool
{
    // No table: the progress lives in the API, the config in glpi_configs.
    return true;
}

function plugin_aiassistant_uninstall(): bool
{
    Config::deleteConfigurationValues('plugin:aiassistant', ['api_url', 'api_key']);
    return true;
}
