// Offline compile fixture for CI. Values are placeholders, never deployed.
// What the portal button supplies: only the parameters without a default. Every other secret comes from its
// parameter default (newGuid), the region from the deployment itself.
using '../main.bicep'

param environmentName = 'ci-portal'
param profile = 'small'
param imageTag = 'v0.0.0-ci'
param seedStaffPassword = 'ci-placeholder-staff'
