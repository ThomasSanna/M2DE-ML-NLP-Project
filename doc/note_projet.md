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