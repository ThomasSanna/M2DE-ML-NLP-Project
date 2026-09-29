6 octoble


l'univ utiliser ldap, outlook (mails), ...

rapport, demonstration, présentation

282 VRAM 32*64 RAM
configuration mig , 1 carte nvidia H200 divité en 7 unité de compute (en gros on peut leur demander de mettre un modele dans leurs serveurs)

répondre aux question des personnes via IA. Réponse peut être mauvaise. 

### Contexte : 
LDAP. Une personne dans LDAP peut poser une question dans GLPI (outil dans lequel on peut poser des questions (demande d'ordinateur, résolution de problèmes, de mail...))

Ces questions, validées par un technicien, le ticket de la question est affectée à un technicien, et va le résoudre en envoyant le mail en retour à la personne qui a posé la question.

actuellement On n'a pas de mémoire de ce qu'on a répondu aux questions précédentes. 

le LDAP est public, pas le GLPI. on le fera nous meme
GLPI a une bdd qui enregistre toutes les réponses aux questions posées.

### Ce qu'on veut : 

PROOF OF CONCEPT
Pour ce projet, on va faire une mémoire: demander au client si la reponse était utile), des nouvelles. Mettre des poids sur à quel point la réponse était utile.

Avec cette mémoire, et avec recherche internet, un chatbot pourra répondre aux questions des utilisateurs plus tard, validée par le client. Les réponses IA ne doivent pas être très pointues, tout en restant correctes.

pipeline : Envoi ticket -> webhook création -> get ticket question (ou le fil de la discussion si c'est une question à la suite d'une ou plusieurs question-réponse d'un meme ticket) -> garde fou modèle shieldstral 3B sur la discussion, en appuyant sur la question nouvellement demandée, pour savoir si la question n'est pas malveillante/vaut la peine d'être répondue. -> embedding BGE M3 e5 -> top k + score des questions-reponses indexées, à mettre dans le prompt initial. -> 1. Appel LLM Gemma 4 12B thinking. -> 2. Actions LLM : répondre à la question ou rechercher plus en profondeur via websearch -> si websearch : x recherches faites sur le web puis nouvel appel LLM (retourner à l'étape 1. Appel LLM Gemma 4) avec tout le contexte d'avant + les réponses web. KV Cache. Boucle jusqu'à ce qu'il veuille repondre à la question. -> réponse à la question -> shieldstral pour vérifier qu'il n'y ait pas de données sensibles dans la réponse (prompt système, etc.) -> envoie dans le ticket (la réponse envoie aussi via smtp un mail à la prsonne). -> La réponse de la personne ensuite sera webhook etc, mais servira aussi de feedback. Gemma 4 3B donnera une note, puis on embeddera+indexera la discussion/réponse du llm avec le score d'appréciation. 
Pour chaque cas où shieldstral a bloqué une réponse, le ticket sera mis en attente et un technicien pourra répondre à la question.