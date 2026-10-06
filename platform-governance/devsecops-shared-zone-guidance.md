# Why a Segregated DevOps and DevSecOps Shared Zone: AWS, NSA and CISA Guidance

Oct 7, 2026 · @Alfadil Elameen

## Purpose

The bank should run its DevOps and DevSecOps capabilities in a dedicated, segregated shared zone, separate from the UAT/Test and Production server farms. AWS and the US NSA and CISA both recommend this separation for CI/CD and security tooling.

This document collects their arguments, with citations, to support the DevSecOps Zone PROD proposed architecture. In that design, one shared zone hosts source control, CI/CD orchestration, the artifact registry, secrets management and the security capabilities. It reaches UAT and Production only through firewall-controlled flows.

## AWS guidance

AWS recommends housing CI/CD management capabilities in a dedicated Deployments OU, separate from production and non-production workload environments ([AWS whitepaper: Advanced OUs](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/advanced-ous.html)). Shared infrastructure and security services also get their own foundational OUs ([AWS Organizations: OU best practices](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_ous_best_practices.html)).

### Why AWS separates CI/CD from workloads

The whitepaper *Organizing Your AWS Environment Using Multiple Accounts* gives these reasons and recommendations ([source](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/advanced-ous.html#deployments-ou)):

- **CI/CD plays critical roles.** CI/CD orchestrates quality validation, security compliance checks, artifact build and promotion, and production release. It therefore needs policies and operational practices that differ from those of the workload environments.
- **Write versus read access.** CI jobs and CD pipelines need write access to publish and promote artifacts. Production workloads should only need read access to pull already-built, promoted artifacts.
- **Pipelines touch both non-production and production.** If CI/CD ran inside production, production would need access to non-production. Centralizing CI/CD in its own accounts avoids that.
- **Unique tooling.** CI/CD depends on tools that workloads don't need. Keeping them in the CI/CD accounts reduces the complexity and attack surface of the workload environments.
- **Build in a production-grade zone.** Because CI jobs and build stages produce the formal candidate artifacts, AWS recommends running them in production CI/CD accounts, not in production workload environments.

### Related AWS principles

- **Foundational shared OUs.** AWS recommends starting with Security and Infrastructure OUs for centralized services that serve the whole organization, such as security tooling, networking and shared IT services ([source](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_ous_best_practices.html#ous_best_practices_foundational)).
- **Production isolation.** Production workloads should be isolated in production OUs, and should not depend on workloads in non-production environments ([source](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/advanced-ous.html#production-and-non-production-workload-environments)).
- **Non-production reads shared services.** Dev and test environments typically need read-only access to shared source code and artifact management services to deploy candidate artifacts ([source](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/advanced-ous.html#non-production-environments-accessing-dependencies)).
- **Different governance model.** AWS suggests a Deployments OU when CI/CD has a governance and operational model that differs from the Prod and SDLC workload OUs ([source](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_ous_best_practices.html#ous_best_practices_recommended)).

One nuance: the same AWS page notes that distributing CI/CD per application reduces dependency on a single central CI/CD team. For a bank with a small DevOps & SRE team serving 170+ applications, one shared zone is the practical choice. Per-application pipeline separation can be achieved inside it with dedicated pipelines, credentials and runners.

## NSA and CISA guidance

NSA and CISA treat the CI/CD environment as its own high-value target and recommend segmenting it from the rest of the network. Their joint Cybersecurity Information Sheet, *Defending Continuous Integration/Continuous Delivery (CI/CD) Environments* (June 2023, TLP:CLEAR), is tool-agnostic and applies to on-premises pipelines as well as cloud ones ([NSA/CISA CSI, PDF](https://media.defense.gov/2023/Jun/28/2003249466/-1/-1/0/CSI_DEFENDING_CI_CD_ENVIRONMENTS.PDF)).

### Why the CI/CD environment needs special protection

- **Separate attack surface.** The guide's conclusion states that "the CI/CD pipeline is a distinct and separate attack surface" from other parts of the software supply chain (p. 15).
- **Impact multiplies.** Attackers who compromise the source of software that is deployed to many environments can multiply the impact several times over (p. 15).
- **Entry point into the network.** A compromised CI/CD environment can give attackers a way into corporate networks and to sensitive data and services (p. 15).
- **Attractive target.** Software supply chains and CI/CD environments are attractive targets, and compromises are increasing (pp. 1, 6).
- **Insecure pipeline, insecure application.** An insecure pipeline easily leads to an insecure application (p. 6).

### Recommendations that support a segregated shared zone

- **Network segmentation and traffic filtering.** Implement robust segmentation between networks and functions to limit malware spread and block access from parts of the network that don't need it. Define a demilitarized zone so there is no unregulated communication between networks (p. 12).
- **Least privilege and separation of duties.** The pipeline should not be accessible to everyone. Developers get access only to the pipelines and components they work on, and source-code and build privileges are separated (p. 11).
- **Secure secrets.** Never pass secrets in plaintext or embed them in software; store them in a secrets management solution and pass them by indirect reference (p. 12).
- **Integrated security scanning.** Run SAST in the build stage, scan every image pulled into the pipeline, and run DAST against a test instance of the newly built application (p. 13).
- **SBOM and SCA.** Track all third-party and open-source components, and have a vulnerability management team correlate SBOM data with known CVEs (p. 14).
- **Centralized patch management.** Keep operating systems, software and CI/CD tools current, using a centralized patch management system with integrity validation (pp. 12–13).
- **Trusted sources only.** Use software, libraries and artifacts only from secure, trusted sources (p. 14).
- **Audit logs.** Record who committed, reviewed and deployed what, when and where (p. 14).
- **Resiliency.** Build the pipeline for high availability and test disaster recovery periodically (p. 15).

### Threat scenarios where segmentation is a named mitigation

The guide's first threat scenario is an attacker obtaining a developer's credentials for the Git repository or CI/CD service. Network segmentation and traffic filtering is listed as a mitigation, alongside least privilege and fewer long-term credentials (p. 8).

## How the guidance maps to the bank's design

Each element of the proposed DevSecOps Zone follows a specific AWS or NSA/CISA recommendation.

| Design element | What it does | Guidance it follows |
| --- | --- | --- |
| Dedicated DevSecOps zone in HO and DR server farms | Hosts source control, CI/CD, artifact registry, secrets management and security capabilities apart from workloads | AWS: separate CI/CD management from workloads (Deployments OU). NSA/CISA: CI/CD is a distinct attack surface |
| Firewalls between the zone and the UAT/Test and PROD segments | Only approved flows (deploy, image pull, secret injection, patching) cross segment boundaries | NSA/CISA: network segmentation, DMZ, traffic filtering (p. 12) |
| No direct UAT ↔ PROD path | Only the shared zone reaches both segments | AWS: production must not depend on or access non-production |
| Write to registry from CI/CD, read-only pull from clusters | Pipelines publish; UAT and PROD only pull signed, scanned images | AWS: CI/CD needs write access; production needs read access only |
| Build once in the zone, promote the certified UAT image to PROD | No rebuild in production | AWS: run CI and build stages in production CI/CD accounts, not in workload environments |
| Central secrets management | Short-lived credentials injected at runtime; prod and non-prod secrets isolated | NSA/CISA: secure secrets, minimize long-term credentials (pp. 10, 12) |
| SAST, SCA, secrets detection, image scanning, DAST | Quality gates in the pipeline; DAST runs against UAT only | NSA/CISA: integrate security scanning; SBOM and SCA (pp. 13–14) |
| Vulnerability management hub | Aggregates findings, tracks SLAs, feeds the quality gate | NSA/CISA: vulnerability management correlates SBOM data with CVEs (p. 14) |
| Patch automation, test ring before production ring | Patches validated in UAT before PROD | NSA/CISA: centralized patch management with integrity validation (p. 12) |
| Feed sync through the internet proxy | Air-gapped zone gets CVE databases and patch content via a controlled path | NSA/CISA: trusted sources only; eliminate unregulated communication (pp. 12, 14) |
| HO ↔ DR replication | Repos, registry, secrets and findings DB replicated | NSA/CISA: high availability and periodic DR testing (p. 15) |

AWS also notes that tooling kept in a dedicated CI/CD environment reduces the complexity and attack surface of the workload environments. In the bank's case that means scanners, build agents and pipeline tooling stay out of the application servers.

## References

1. Amazon Web Services. *Organizing Your AWS Environment Using Multiple Accounts*, AWS Whitepaper, section "Advanced OUs" (Deployments OU; Separating CI/CD management capabilities from workloads). [docs.aws.amazon.com](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/advanced-ous.html)
2. Amazon Web Services. *Best practices for managing organizational units (OUs) with AWS Organizations*, AWS Organizations User Guide. [docs.aws.amazon.com](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_ous_best_practices.html)
3. National Security Agency and Cybersecurity and Infrastructure Security Agency. *Defending Continuous Integration/Continuous Delivery (CI/CD) Environments*, Cybersecurity Information Sheet U/OO/170159-23, June 2023, Ver. 1.0, TLP:CLEAR. [media.defense.gov (PDF)](https://media.defense.gov/2023/Jun/28/2003249466/-1/-1/0/CSI_DEFENDING_CI_CD_ENVIRONMENTS.PDF)
4. CISA. *CISA and NSA Release Joint Guidance on Defending Continuous Integration/Continuous Delivery (CI/CD) Environments*, alert, 28 June 2023. [cisa.gov](https://cisa.gov/news-events/alerts/2023/06/28/cisa-and-nsa-release-joint-guidance-defending-continuous-integrationcontinuous-delivery-cicd)

Page numbers in the NSA and CISA sections refer to the CSI PDF (reference 3).
