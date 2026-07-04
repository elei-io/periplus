import atlasLogo from "@/assets/atlas-logo.svg"
import { navigationGroups } from "@/lib/navigation"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar"

type AppSidebarProps = {
  pathname: string
  onNavigate: (href: string) => void
}

export function AppSidebar({ pathname, onNavigate }: AppSidebarProps) {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-3 py-3">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" tooltip="Atlas">
              <img
                src={atlasLogo}
                alt=""
                aria-hidden="true"
                className="size-5 shrink-0"
              />
              <span className="font-medium group-data-[collapsible=icon]:hidden">
                Atlas
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarSeparator />
      <SidebarContent>
        {navigationGroups.map((group) => (
          <SidebarGroup key={group.slug}>
            <SidebarGroupLabel>{group.name}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const ItemIcon = item.icon

                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={pathname === item.href}
                        render={
                          <a
                            href={item.href}
                            onClick={(event) => {
                              event.preventDefault()
                              onNavigate(item.href)
                            }}
                          >
                            <ItemIcon />
                            <span>{item.name}</span>
                          </a>
                        }
                        tooltip={item.name}
                      />
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
