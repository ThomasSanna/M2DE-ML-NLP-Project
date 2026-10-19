-- Feedback utilisateur par ticket, issu des enquêtes de satisfaction GLPI.
-- Source du facteur w1 (feedback_utilisateur) du score de la mémoire.
-- Une ligne par ticket clos ayant une enquête (répondue ou non).
SELECT
    t.id                                   AS ticket_id,
    t.name                                 AS titre,
    c.completename                         AS categorie,
    t.content                              AS question,
    sol.content                            AS solution,
    sol.status                             AS solution_statut,   -- 2 = en attente, 3 = acceptée, 4 = refusée
    t.solvedate                            AS date_resolution,
    t.closedate                            AS date_cloture,
    s.date_begin                           AS enquete_envoyee_le,
    s.date_answered                        AS enquete_repondue_le,
    s.satisfaction                         AS note,               -- 0..5, NULL = pas de réponse
    s.satisfaction_scaled_to_5 / 5         AS feedback_normalise, -- 0..1, valeur de w1
    s.comment                              AS commentaire
FROM glpi_tickets t
JOIN glpi_ticketsatisfactions s  ON s.tickets_id = t.id
LEFT JOIN glpi_itilcategories c  ON c.id = t.itilcategories_id
LEFT JOIN glpi_itilsolutions sol ON sol.id = (
    SELECT MAX(id) FROM glpi_itilsolutions
    WHERE itemtype = 'Ticket' AND items_id = t.id
)
WHERE t.is_deleted = 0
ORDER BY t.id;
