import { Compass } from "lucide-react"
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
} from "@/components/ui/sidebar"

type AppSidebarProps = {
  pathname: string
  onNavigate: (href: string) => void
}

export function AppSidebar({ pathname, onNavigate }: AppSidebarProps) {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-14 shrink-0 justify-center border-b px-3 py-0">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              tooltip="Periplus Admin"
              render={
                <a
                  href="/"
                  aria-label="Periplus Admin home"
                  className="gap-[9px]"
                  onClick={(event) => {
                    event.preventDefault()
                    onNavigate("/")
                  }}
                >
                  <Compass
                    aria-hidden="true"
                    strokeWidth={1.5}
                    className="size-[26px]! shrink-0 group-data-[collapsible=icon]:size-5!"
                  />
                  <span className="text-[22px] font-[650] tracking-[-0.9px] group-data-[collapsible=icon]:hidden">
                    periplus
                  </span>
                  <span className="text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
                    Admin
                  </span>
                </a>
              }
            />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
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
    </Sidebar>
  )
}
