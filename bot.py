# Find brugernavn i logningen og embed
username = getattr(interaction.user, 'display_name', interaction.user.name)
name = getattr(member, 'display_name', member.name)
