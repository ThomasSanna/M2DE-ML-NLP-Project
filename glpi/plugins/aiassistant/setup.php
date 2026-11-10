<?php

/**
 * Plugin « Assistant IA » : progression de la réponse et panneau des sources
 * dans la chronologie du ticket.
 *
 * GLPI sert `public/*` d'un plugin sous `/{plugin}/{fichier}` : les noms donnés
 * aux hooks ADD_JAVASCRIPT / ADD_CSS sont donc relatifs à `public/`.
 */

use Glpi\Plugin\Hooks;

define('PLUGIN_AIASSISTANT_VERSION', '1.0.0');

function plugin_init_aiassistant(): void
{
    global $PLUGIN_HOOKS;

    $PLUGIN_HOOKS[Hooks::CSRF_COMPLIANT]['aiassistant'] = true;
    $PLUGIN_HOOKS[Hooks::ADD_JAVASCRIPT]['aiassistant'] = ['aiassistant.js'];
    $PLUGIN_HOOKS[Hooks::ADD_CSS]['aiassistant'] = ['aiassistant.css'];
}

function plugin_version_aiassistant(): array
{
    return [
        'name'         => 'Assistant IA',
        'version'      => PLUGIN_AIASSISTANT_VERSION,
        'author'       => 'M2DE',
        'license'      => 'GPL-3.0-or-later',
        'homepage'     => 'https://github.com/ThomasSanna/M2DE-ML-NLP-Project',
        'requirements' => [
            'glpi' => [
                'min' => '11.0.0',
                'max' => '12.0.0',
            ],
        ],
    ];
}

function plugin_aiassistant_check_prerequisites(): bool
{
    return true;
}
