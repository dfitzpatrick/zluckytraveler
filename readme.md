
### Required Environment Variables
`TOKEN` - Discord Bot Token

### To Run the Bot

#### REGARDING ENVIRONMENT VARIABLES.
The bot can read the .env file that is supplied in this ZIP file. You can put your environment variables there in
the KEY=VALUE format.


OR you can delete it and pass your ENVIRONMENT VARIABLES manually in the run command with the -e flag.


Open your terminal to the directory where you unzipped the files

Build the image: `docker build .`

Run the image: `docker run -it IMAGE_ID`  (using .env)

Run the image: `docker run -it -e TOKEN=mybottokenhere IMAGE_ID`  (manual)

This puts you in interactive mode to see the output etc. You can run in detached after seeing it.


### Sync Application Commands

Discord takes 2 hours to register your commands. To speed this up, you can use a custom sync.

To Sync App Commands to your guild, use the text command `@MyBotName sync *`
To keep your bot from needing the message_content priviliged intents, the bot only responds to messages whenmentioned to.


This needs to only be ran once.

### Other Considerations
Typically GUILD OWNER type commands are not recommended as they take up spots  in the command tree and all users can see them. We
could easily transition these to text commands as mentioned above or we can keep it as it is.
