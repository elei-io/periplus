import periplusMark from "@/assets/periplus-mark.svg"
import periplusWordmark from "@/assets/periplus-wordmark.svg"
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
              tooltip="Periplus"
              render={
                <a
                  href="/"
                  onClick={(event) => {
                    event.preventDefault()
                    onNavigate("/")
                  }}
                >
                  <img
                    src={periplusWordmark}
                    alt="Periplus"
                    className="h-5 w-auto group-data-[collapsible=icon]:hidden"
                  />
                  <img
                    src={periplusMark}
                    alt="Periplus"
                    className="hidden size-5 shrink-0 group-data-[collapsible=icon]:block"
                  />
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
