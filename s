ldap_users = User.where(
  id: Identity.where("provider LIKE ?", "ldap%").select(:user_id)
)

puts "ID\tUSERNAME\tEMAIL\tSTATE\tLDAP_PROVIDERS"

ldap_users.includes(:identities).find_each do |user|
  providers = user.identities
    .select { |identity| identity.provider.to_s.start_with?("ldap") }
    .map(&:provider)
    .uniq
    .join(",")

  puts [user.id, user.username, user.email, user.state, providers].join("\t")
end

puts "Total LDAP users: #{ldap_users.count}"